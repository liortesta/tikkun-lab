"""Transport to the model fleet — Kie AI, plus OpenRouter when a key is present.

Deliberately thin. Everything that decides *what* to ask lives in `lab.py`, and
everything that decides what counts as an answer lives in `guard.py`.

Keys come from the environment only. Never hardcode one here — this repository is
meant to be published.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

KIE_BASE = "https://api.kie.ai"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

_PROVIDER_KEYS = ("KIE_API_KEY", "OPENROUTER_API_KEY")


def load_dotenv(path: str | None = None) -> list[str]:
    """Read provider keys from a .env file if they are not already in the environment.

    Looks in the project root by default. Real environment variables always win, so
    a shell export overrides the file. Returns the names of the keys it filled in.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".env")
    if not os.path.isfile(path):
        return []

    filled = []
    # utf-8-sig, not utf-8: PowerShell's `Set-Content -Encoding utf8` writes a
    # byte-order mark, which otherwise glues itself to the first key's name so
    # that key silently never loads while the rest of the file works fine.
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip().lstrip("﻿")
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip().strip("'\"")
            if name in _PROVIDER_KEYS and value and not os.environ.get(name):
                os.environ[name] = value
                filled.append(name)
    return filled


def providers() -> dict[str, bool]:
    """Which providers have a key. `available()` only says *some* model can run —
    this says which, and a model list is useless without that."""
    return {"kie": bool(os.environ.get("KIE_API_KEY")),
            "openrouter": bool(os.environ.get("OPENROUTER_API_KEY"))}


def reachable(model: str) -> bool:
    """Whether this specific model can be called with the keys present."""
    return providers()["openrouter" if "/" in model else "kie"]


load_dotenv()

#: model -> (endpoint kind, $/1M input, $/1M output)
MODELS: dict[str, tuple[str, float, float]] = {
    "claude-fable-5": ("claude", 4.00, 20.00),
    "claude-opus-4-8": ("claude", 2.00, 10.00),
    "claude-sonnet-5": ("claude", 0.85, 4.275),
    "claude-haiku-4-5": ("claude", 0.275, 1.425),
    "gpt-5-6-sol": ("codex", 1.40, 8.40),
    "gpt-5-6-luna": ("codex", 0.28, 1.68),
    "grok-4-5": ("grok", 0.80, 2.40),
    "grok-4-3": ("grok", 0.08, 1.00),
    "gemini-3-5-flash": ("gemini", 0.45, 2.70),
    "gemini-3.1-pro": ("gemini-openai", 0.50, 3.50),
}

# Role defaults. Reasoning about mechanism is worth a strong model; summarising an
# engine table that already holds the numbers is not.
CHEAP, MID, SMART, JUDGE = "grok-4-3", "gpt-5-6-luna", "claude-fable-5", "claude-opus-4-8"


class FleetError(RuntimeError):
    """Raised when no model in the fleet could answer."""


@dataclass
class Usage:
    """Running tally, so a session can report what it cost."""

    calls: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    def record(self, model: str) -> None:
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0) + 1

    def summary(self) -> str:
        if not self.calls:
            return "no model calls"
        parts = ", ".join(f"{m}x{n}" for m, n in sorted(self.by_model.items()))
        return f"{self.calls} model calls ({parts})"


USAGE = Usage()


def available() -> bool:
    """Whether any provider key is configured."""
    return bool(os.environ.get("KIE_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


def _post(base: str, path: str, body: dict, headers: dict, timeout: int = 180) -> str:
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        raise FleetError(f"{exc.code} from {path}: {exc.read()[:300].decode(errors='replace')}") from exc
    except Exception as exc:
        raise FleetError(f"{type(exc).__name__} calling {path}: {exc}") from exc


def _sse_text(raw: str) -> str:
    """Collect assistant text out of a Responses-API SSE stream."""
    parts = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if event.get("type") == "response.output_text.done":
            parts.append(event.get("text", ""))
    return "\n".join(dict.fromkeys(parts))


def _kie(model: str, prompt: str, system: str, max_tokens: int, temperature: float) -> str:
    key = os.environ.get("KIE_API_KEY")
    if not key:
        raise FleetError("KIE_API_KEY is not set")
    # Cloudflare in front of Kie rejects the default urllib user agent outright.
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": "curl/8.4.0", "Accept": "*/*"}
    kind = MODELS[model][0]

    if kind == "claude":
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        payload = json.loads(_post(KIE_BASE, "/claude/v1/messages", body,
                                   {**headers, "anthropic-version": "2023-06-01"}))
        return "".join(block.get("text", "") for block in payload.get("content", []))

    merged = (f"<system>\n{system}\n</system>\n\n" if system else "") + prompt

    if kind in ("codex", "grok"):
        raw = _post(KIE_BASE, f"/{kind}/v1/responses",
                    {"model": model, "input": [{"role": "user", "content": [
                        {"type": "input_text", "text": merged}]}]}, headers)
        return _sse_text(raw)

    if kind == "gemini":
        raw = _post(KIE_BASE, f"/gemini/v1/models/{model}:streamGenerateContent",
                    {"contents": [{"role": "user", "parts": [{"text": merged}]}],
                     "generationConfig": {"temperature": temperature}}, headers)
        try:
            chunks = json.loads(raw)
        except json.JSONDecodeError:
            chunks = [json.loads(line[5:].strip()) for line in raw.splitlines()
                      if line.startswith("data:")]
        return "".join(part.get("text", "")
                       for chunk in chunks
                       for candidate in chunk.get("candidates", [])
                       for part in candidate.get("content", {}).get("parts", []))

    if kind == "gemini-openai":
        body = {"model": model, "temperature": temperature,
                "messages": ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": prompt}]}
        payload = json.loads(_post(KIE_BASE, "/gemini-3.1-pro/v1/chat/completions",
                                   body, headers))
        return payload["choices"][0]["message"]["content"]

    raise FleetError(f"unknown endpoint kind {kind!r} for {model!r}")


def _openrouter(model: str, prompt: str, system: str, max_tokens: int,
                temperature: float) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise FleetError("OPENROUTER_API_KEY is not set")
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}]}
    payload = json.loads(_post(OPENROUTER_BASE, "/chat/completions", body,
                               {"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"}))
    return payload["choices"][0]["message"]["content"]


#: Where to go when a model is unavailable. Kept within the same capability tier —
#: a fallback that silently downgrades a judge to a cheap model would change the
#: result while looking like it worked.
FALLBACKS: dict[str, tuple[str, ...]] = {
    "claude-fable-5": ("claude-opus-4-8", "gpt-5-6-sol"),
    "claude-opus-4-8": ("claude-fable-5", "gpt-5-6-sol"),
    "claude-sonnet-5": ("claude-opus-4-8", "gemini-3.1-pro"),
    "claude-haiku-4-5": ("grok-4-3", "gemini-3-5-flash"),
    "gpt-5-6-sol": ("claude-opus-4-8", "gemini-3.1-pro"),
    "gpt-5-6-luna": ("grok-4-5", "gemini-3-5-flash"),
    "grok-4-5": ("gpt-5-6-luna", "gemini-3-5-flash"),
    "grok-4-3": ("gemini-3-5-flash", "claude-haiku-4-5"),
    "gemini-3-5-flash": ("grok-4-3", "claude-haiku-4-5"),
    "gemini-3.1-pro": ("claude-opus-4-8", "gpt-5-6-sol"),
}

_RETRYABLE = ("500", "502", "503", "504", "429", "timed out", "timeout",
              "Connection", "empty response")


def _attempt(model: str, prompt: str, system: str, max_tokens: int,
             temperature: float) -> str:
    text = (_openrouter if "/" in model else _kie)(
        model, prompt, system, max_tokens, temperature)
    USAGE.record(model)
    if not text.strip():
        raise FleetError(f"{model} returned an empty response")
    return text


def call(model: str, prompt: str, system: str = "", max_tokens: int = 2000,
         temperature: float = 0.0, attempts: int = 2) -> str:
    """Raw text from one model. A slash in the name routes to OpenRouter.

    Retries transient failures, then falls back to a model of comparable
    capability. Providers return 500s often enough that a single unlucky call
    would otherwise lose a whole session's judging step.
    """
    errors: list[str] = []
    for candidate in (model, *FALLBACKS.get(model, ())):
        for attempt in range(attempts):
            try:
                return _attempt(candidate, prompt, system, max_tokens, temperature)
            except FleetError as exc:
                errors.append(f"{candidate}: {exc}")
                message = str(exc)
                if not any(marker in message for marker in _RETRYABLE):
                    break  # a real rejection, not a blip — move to the next model
                if attempt + 1 < attempts:
                    time.sleep(1.5 * (attempt + 1))
    raise FleetError("no model in the fleet answered — " + " | ".join(errors[-3:]))


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict | list:
    """Pull a JSON value out of a model response.

    Models wrap JSON in prose and fences no matter how firmly the prompt says not
    to, so parse forgivingly rather than failing the whole run over a code fence.
    """
    fenced = _FENCE.search(text)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)

    for chunk in candidates:
        chunk = chunk.strip()
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = chunk.find(opener), chunk.rfind(closer)
            if 0 <= start < end:
                try:
                    return json.loads(chunk[start:end + 1])
                except json.JSONDecodeError:
                    continue
    raise FleetError(f"no JSON found in response: {text[:200]!r}")


def ask_json(model: str, prompt: str, system: str = "", schema: str = "",
             retries: int = 1, max_tokens: int = 2000) -> dict | list:
    """Call a model and insist on JSON back, re-asking once with the parse error."""
    full = prompt if not schema else (
        f"{prompt}\n\n<output_schema>\n{schema}\n</output_schema>\n"
        "Reply with JSON matching the schema and nothing else.")
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return extract_json(call(model, full, system, max_tokens))
        except FleetError as exc:
            last = exc
            full = (f"{full}\n\n<previous_attempt_failed>\n{exc}\n"
                    "Return only valid JSON.\n</previous_attempt_failed>")
    raise FleetError(f"{model} produced no usable JSON after "
                     f"{retries + 1} attempts: {last}")
