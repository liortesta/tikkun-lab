"""Tikkun Lab — the local application.

    python app.py            start on http://127.0.0.1:8756 and open a browser
    python app.py --port N   pick a different port
    python app.py --no-open  don't launch a browser

Standard library only, so there is nothing to install beyond numpy and scipy.

The work is split between the browser and this server on one principle: whatever
has to feel instant runs in the browser, whatever cannot run there runs here.

  browser   the JavaScript engine port (web/engine.js), driving every slider at
            about a millisecond a frame. It is not a second opinion — it is the
            same equations, checked value-by-value against the Python engine by
            web/verify.mjs, and tests/test_web.py fails if it ever falls behind.
  server    the agent army, which needs API keys and 30-60 seconds; the Python
            engine for authoritative batch runs; and the experiment log.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import threading
import traceback
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agents import Intervention, InterventionError, run_protocol
from agents import client as fleet
from agents.lab import Lab, _outcome_json
from engine import MILK, PATIENTS, Patient, challenge, dose_response, eliciting_dose

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
LOG_PATH = os.path.join(ROOT, "experiments.jsonl")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def clean(value):
    """JSON has no infinity. Send a string the browser knows how to read back."""
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def patient_from(raw: dict) -> Patient:
    """Build a Patient from request JSON, falling back to the default preset."""
    base = PATIENTS.get(raw.get("preset", ""), PATIENTS["default"])
    fields = {}
    for name in ("specific_ige_ku", "total_ige_ku", "igg4_m", "treg", "mucosal_barrier"):
        if name in raw:
            try:
                value = float(raw[name])
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative number")
            fields[name] = value
    label = str(raw.get("label", base.label))[:60]
    return replace(base, label=label, **fields)


def steps_from(raw: list) -> list[Intervention]:
    return [Intervention.from_dict(step) for step in (raw or [])]


# ---------------------------------------------------------------------------
# API handlers. Each takes the parsed request body and returns JSON-ready data.
# ---------------------------------------------------------------------------

def api_health(_body: dict) -> dict:
    providers = fleet.providers()
    return {
        "ok": True,
        "fleet_available": fleet.available(),
        "providers": providers,
        # Every model the UI offers is a Kie model, so an OpenRouter key alone
        # leaves the agent panel looking connected while every call fails.
        "default_models_reachable": providers["kie"],
        "parameters": len(list(MILK)),
        "trust": MILK.trust_score(),
        "provenance": MILK.counts(),
    }


def api_params(_body: dict) -> list:
    return [{"name": name, "value": p.value, "unit": p.unit,
             "provenance": p.provenance.value, "source": p.source, "note": p.note}
            for name, p in MILK.items()]


def api_patients(_body: dict) -> dict:
    return {name: {"specific_ige_ku": p.specific_ige_ku,
                   "total_ige_ku": p.total_ige_ku,
                   "igg4_m": p.igg4_m, "treg": p.treg,
                   "mucosal_barrier": p.mucosal_barrier,
                   "eliciting_dose_mg": clean(eliciting_dose(p))}
            for name, p in PATIENTS.items()}


def api_challenge(body: dict) -> dict:
    patient = patient_from(body.get("patient", {}))
    dose = float(body.get("dose_mg", 25.0))
    result = challenge(patient, dose, keep_trace=bool(body.get("trace")))
    out = {k: clean(v) for k, v in vars(result).items() if not k.startswith("trace")}
    if result.trace_t is not None:
        out["trace"] = [[float(t), float(h)]
                        for t, h in zip(result.trace_t, result.trace_histamine)]
    return out


def api_threshold(body: dict) -> dict:
    patient = patient_from(body.get("patient", {}))
    return {"eliciting_dose_mg": clean(eliciting_dose(patient))}


def api_curve(body: dict) -> dict:
    patient = patient_from(body.get("patient", {}))
    doses, scores = dose_response(
        patient, lo_mg=float(body.get("lo_mg", 1e-2)),
        hi_mg=float(body.get("hi_mg", 1.6e4)), points=int(body.get("points", 46)))
    return {"doses": doses.tolist(), "scores": scores.tolist(),
            "eliciting_dose_mg": clean(eliciting_dose(patient))}


def api_protocol(body: dict) -> dict:
    patient = patient_from(body.get("patient", {}))
    steps = steps_from(body.get("steps"))
    outcome = run_protocol(patient, steps, str(body.get("label", "protocol"))[:60])
    return _outcome_json(outcome)


def api_protocols(body: dict) -> list:
    """The standing comparison panel, recomputed for whichever patient is loaded."""
    patient = patient_from(body.get("patient", {}))
    panel = [
        ("אימונותרפיה 30 מ״ג/יום",
         [{"kind": "oral_immunotherapy", "params": {"daily_dose_mg": 30, "days": 365}}]),
        ("אימונותרפיה 300 מ״ג/יום",
         [{"kind": "oral_immunotherapy", "params": {"daily_dose_mg": 300, "days": 365}}]),
        ("אימונותרפיה 1000 מ״ג/יום",
         [{"kind": "oral_immunotherapy", "params": {"daily_dose_mg": 1000, "days": 365}}]),
        ("אנטי-IgE ואז אימונותרפיה",
         [{"kind": "anti_ige", "params": {"free_ige_reduction": 0.95}},
          {"kind": "oral_immunotherapy", "params": {"daily_dose_mg": 300, "days": 365}}]),
        ("נוגדן חוסם פסיבי",
         [{"kind": "passive_igg4", "params": {"titre_mg_l": 120}}]),
    ]
    return [_outcome_json(run_protocol(patient, steps_from(steps), label))
            for label, steps in panel]


def api_log_read(_body: dict) -> list:
    if not os.path.isfile(LOG_PATH):
        return []
    entries = []
    with open(LOG_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries[-200:]


def api_log_write(body: dict) -> dict:
    entry = {"saved_at": body.get("saved_at"), "note": str(body.get("note", ""))[:400],
             "patient": body.get("patient"), "steps": body.get("steps"),
             "result": body.get("result")}
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"saved": True, "count": len(api_log_read({}))}


def api_log_clear(_body: dict) -> dict:
    if os.path.isfile(LOG_PATH):
        os.remove(LOG_PATH)
    return {"cleared": True}


ROUTES = {
    "/api/health": api_health,
    "/api/params": api_params,
    "/api/patients": api_patients,
    "/api/challenge": api_challenge,
    "/api/threshold": api_threshold,
    "/api/curve": api_curve,
    "/api/protocol": api_protocol,
    "/api/protocols": api_protocols,
    "/api/log": api_log_read,
    "/api/log/save": api_log_write,
    "/api/log/clear": api_log_clear,
}


# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "TikkunLab"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if not self.server.verbose:
            return
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")
        # stderr is block-buffered when redirected to a file, so without this a
        # log kept for debugging lags minutes behind the requests it describes.
        sys.stderr.flush()

    def handle_one_request(self):
        # A browser closing a keep-alive connection, or navigating away from a
        # streaming response, raises here. That is normal client behaviour, not a
        # server fault, and letting socketserver print a traceback for it buries
        # the real errors.
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # -- helpers --

    def _send(self, code: int, body: bytes, content_type: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # A local research tool. Nothing here should be reachable from a page the
        # user did not open themselves.
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200):
        body = json.dumps(clean(payload), ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, message: str):
        self._json({"error": message}, code)

    def _static(self, relative: str):
        safe = os.path.normpath(relative).lstrip("\\/")
        path = os.path.join(WEB, safe)
        # Refuse anything that escapes web/ — this server binds to localhost but
        # a path traversal would still hand out arbitrary files.
        if not os.path.abspath(path).startswith(os.path.abspath(WEB) + os.sep):
            return self._error(403, "forbidden")
        if not os.path.isfile(path):
            return self._error(404, f"not found: {relative}")
        with open(path, "rb") as handle:
            body = handle.read()
        kind = CONTENT_TYPES.get(os.path.splitext(path)[1], "application/octet-stream")
        self._send(200, body, kind, {"Cache-Control": "no-store"})

    # -- routing --

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path

        if route in ("/", "/index.html"):
            return self._static("app.html")
        if route == "/api/lab/stream":
            return self._stream_lab(parse_qs(parsed.query))
        if route in ROUTES:
            try:
                return self._json(ROUTES[route]({}))
            except Exception as exc:
                traceback.print_exc()
                return self._error(500, f"{type(exc).__name__}: {exc}")
        if route.startswith("/api/"):
            return self._error(404, f"no such endpoint: {route}")
        return self._static(route.lstrip("/"))

    def do_POST(self):
        route = urlparse(self.path).path

        # Read the body before deciding anything, including whether the route
        # exists. On a keep-alive connection an unread body stays in the socket
        # buffer and the next request parses it as a request line — which
        # surfaces as a connection reset rather than the 404 that was sent.
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            self.close_connection = True
            return self._error(413, "request too large")
        raw = self.rfile.read(length) if length else b"{}"

        handler = ROUTES.get(route)
        if handler is None:
            return self._error(404, f"no such endpoint: {route}")

        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return self._error(400, f"invalid JSON: {exc}")

        try:
            return self._json(handler(body))
        except (InterventionError, ValueError) as exc:
            return self._error(400, str(exc))
        except Exception as exc:
            traceback.print_exc()
            return self._error(500, f"{type(exc).__name__}: {exc}")

    # -- the agent army, streamed --

    def _stream_lab(self, params: dict):
        """Server-sent events, because a live agent session takes 30-60 seconds
        and showing nothing for a minute reads as a hang."""
        goal = (params.get("goal", [""])[0] or
                "Raise this patient's threshold enough that an accidental exposure is safe")
        offline = params.get("offline", ["0"])[0] == "1"
        count = max(2, min(6, int(params.get("count", ["4"])[0] or 4)))
        try:
            patient = patient_from(json.loads(params.get("patient", ["{}"])[0]))
        except (json.JSONDecodeError, ValueError) as exc:
            return self._error(400, f"bad patient: {exc}")

        models = {
            "pi_model": params.get("pi", ["claude-fable-5"])[0],
            "review_model": params.get("review", ["gpt-5-6-luna"])[0],
            "judge_model": params.get("judge", ["claude-opus-4-8"])[0],
        }

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        events: queue.Queue = queue.Queue()

        def worker():
            try:
                lab = Lab(patient, offline=offline, **models)
                lab.run(goal, count=count, progress=lambda s, p: events.put((s, p)))
            except Exception as exc:
                traceback.print_exc()
                events.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                events.put((None, None))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        try:
            while True:
                stage, payload = events.get()
                if stage is None:
                    break
                chunk = (f"event: {stage}\n"
                         f"data: {json.dumps(clean(payload), ensure_ascii=False)}\n\n")
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass  # the browser navigated away mid-session
        finally:
            self.close_connection = True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tikkun Lab — local application")
    parser.add_argument("--port", type=int, default=8756)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--env", help="path to a .env holding provider API keys")
    args = parser.parse_args(argv)

    loaded = fleet.load_dotenv(args.env) if args.env else fleet.load_dotenv()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.verbose = args.verbose
    url = f"http://{args.host}:{args.port}"

    print("=" * 62)
    print("  TIKKUN LAB")
    print("=" * 62)
    print(f"  {url}")
    print()
    print(f"  engine        {len(list(MILK))} parameters, "
          f"{MILK.trust_score():.0%} provenance strength")
    if fleet.available():
        source = f" (keys from {', '.join(loaded)})" if loaded else ""
        print(f"  agent army    ready{source}")
    else:
        print("  agent army    no API key — the lab bench works, agents run offline")
        print("                set KIE_API_KEY or OPENROUTER_API_KEY, or copy .env.example")
    print()
    print("  Ctrl+C to stop")
    print("=" * 62)

    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
