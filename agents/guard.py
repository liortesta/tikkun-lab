"""Catch a model inventing a number.

The rule this project rests on — the engine produces the numbers, the agents
interpret them — is easy to state and easy to violate. A model asked to explain
a result will helpfully add "histamine peaked around 8 ng/mL" when nothing
measured that, and the sentence reads exactly like the ones that are true.

So every number in an agent's output is checked against the numbers it was
given. Anything that cannot be traced back is flagged. The guard does not block
the run; it annotates the report, because a flagged sentence is usually a model
rounding or restating rather than fabricating, and a human should see which.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Matches integers, decimals and scientific notation, with optional sign.
_NUMBER = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")

#: Numbers too common to be evidence of anything.
_TRIVIAL = {0.0, 1.0, 2.0, 3.0, 10.0, 100.0}


@dataclass(frozen=True)
class Claim:
    value: float
    context: str

    def render(self) -> str:
        return f"{self.value:g}  in: …{self.context.strip()}…"


def _parse(token: str) -> float | None:
    try:
        value = float(token.replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def extract_numbers(text: str) -> list[tuple[float, str]]:
    """Every number in `text`, each with the phrase around it."""
    found = []
    for match in _NUMBER.finditer(text):
        value = _parse(match.group())
        if value is None:
            continue
        start = max(0, match.start() - 45)
        end = min(len(text), match.end() + 45)
        found.append((value, text[start:end].replace("\n", " ")))
    return found


def _matches(value: float, allowed: float, rel: float) -> bool:
    """Whether `value` is `allowed`, allowing for the model having rounded it."""
    if allowed == 0.0:
        return abs(value) < 1e-9
    if math.isclose(value, allowed, rel_tol=rel, abs_tol=1e-12):
        return True
    # A model writing "raised the threshold 100-fold" from 99.87 is restating,
    # not inventing. Accept the value rounded to 1, 2 or 3 significant figures.
    for digits in (1, 2, 3):
        if allowed != 0.0:
            magnitude = math.floor(math.log10(abs(allowed)))
            quantum = 10.0 ** (magnitude - digits + 1)
            if abs(value - round(allowed / quantum) * quantum) < quantum * 0.51:
                return True
    return False


def audit_text(text: str, allowed: list[float], rel: float = 0.02) -> list[Claim]:
    """Numbers in `text` that do not trace back to any value in `allowed`."""
    permitted = [v for v in allowed if v is not None and math.isfinite(v)]
    untraceable = []
    for value, context in extract_numbers(text):
        if value in _TRIVIAL:
            continue
        if 1900 <= value <= 2100 and float(value).is_integer():
            continue  # a citation year, not a measurement
        if any(_matches(value, candidate, rel) for candidate in permitted):
            continue
        untraceable.append(Claim(value, context))
    return untraceable


def allowed_from(*sources: object) -> list[float]:
    """Collect quotable numbers from engine output and from prompt text.

    Anything the agent was shown is fair to repeat. Anything else is not.
    """
    values: list[float] = []
    for source in sources:
        if source is None:
            continue
        if isinstance(source, (int, float)) and not isinstance(source, bool):
            values.append(float(source))
        elif isinstance(source, str):
            values.extend(v for v, _ in extract_numbers(source))
        elif isinstance(source, dict):
            values.extend(allowed_from(*source.values()))
        elif isinstance(source, (list, tuple, set)):
            values.extend(allowed_from(*source))
        elif hasattr(source, "numbers"):
            values.extend(float(v) for v in source.numbers() if math.isfinite(v))
    return values


def report(claims: list[Claim], limit: int = 6) -> str:
    if not claims:
        return "  all numeric claims trace back to engine output"
    lines = [f"  {len(claims)} untraceable numeric claim(s):"]
    for claim in claims[:limit]:
        lines.append(f"    - {claim.render()}")
    if len(claims) > limit:
        lines.append(f"    ... and {len(claims) - limit} more")
    return "\n".join(lines)
