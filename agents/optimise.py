"""Search a protocol's numbers for the best version of itself.

The agents choose *which* mechanisms to combine. Choosing the doses is a search
problem, and search is something the engine should do rather than a language
model — it is exactly the kind of question where a model would produce a
confident number and the engine can produce a correct one.

The objective is not simply "largest threshold shift". Every specialist review
run so far has raised the same objection: a maintenance dose that sits above the
patient's own eliciting dose would provoke a reaction on day one. So that is
encoded as a hard constraint rather than left as advice, and a protocol that
violates it is rejected no matter how well it scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from engine import MILK, Patient, Registry, challenge, eliciting_dose

from .protocol import GLASS_OF_MILK_MG, LEVERS, Intervention, Outcome, run_protocol


@dataclass
class Candidate:
    steps: list[Intervention]
    outcome: Outcome
    score: float
    safe_start: bool

    def describe(self) -> str:
        return "; ".join(step.describe() for step in self.steps)


@dataclass
class OptimisationResult:
    baseline: Candidate
    best: Candidate
    evaluations: int = 0
    rejected_unsafe: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        """Turning an unsafe protocol into a safe one counts, even at the same
        score — that is often the only thing the search can change once the
        threshold shift has already saturated."""
        if self.best.safe_start and not self.baseline.safe_start:
            return True
        return self.best.score > self.baseline.score + 1e-9

    @property
    def gain(self) -> float:
        if self.baseline.outcome.fold_shift <= 0:
            return math.inf
        return self.best.outcome.fold_shift / self.baseline.outcome.fold_shift


def _grid(low: float, high: float, current: float, points: int = 7) -> list[float]:
    """Candidate values spanning the lever's range, log-spaced when it spans
    orders of magnitude, always including the value we started from."""
    values = []
    if low > 0 and high / max(low, 1e-12) >= 100:
        lo, hi = math.log10(low), math.log10(high)
        values = [10.0 ** (lo + (hi - lo) * i / (points - 1)) for i in range(points)]
    else:
        values = [low + (high - low) * i / (points - 1) for i in range(points)]
    values.append(current)
    return sorted({round(v, 6) for v in values if low <= v <= high})


def starting_dose_is_safe(patient: Patient, steps: list[Intervention],
                          params: Registry) -> bool:
    """Would day one of this protocol already trigger a reaction?

    Only oral immunotherapy actually feeds the patient allergen. Anything that
    precedes it in the protocol — anti-IgE, blocking antibody, barrier repair —
    has already taken effect by then, so the check runs against the patient as
    the earlier steps leave them, not as they started.
    """
    from .protocol import apply_intervention

    current, current_params = patient, params
    for step in steps:
        if step.kind == "oral_immunotherapy":
            if challenge(current, step.params["daily_dose_mg"], current_params).reaction:
                return False
        current, current_params = apply_intervention(step, current, current_params)
    return True


def score_protocol(patient: Patient, steps: list[Intervention],
                   params: Registry) -> tuple[float, Outcome, bool]:
    """Higher is better. Protection against a full glass is worth more than any
    amount of threshold shift that still leaves a serving dangerous."""
    outcome = run_protocol(patient, steps, "candidate", params)
    safe = starting_dose_is_safe(patient, steps, params)

    shift = outcome.fold_shift
    score = math.log10(shift) if math.isfinite(shift) and shift > 0 else 12.0
    if outcome.protects_against_a_glass:
        score += 4.0
    return score, outcome, safe


def optimise(
    patient: Patient,
    steps: list[Intervention],
    params: Registry = MILK,
    passes: int = 2,
    points: int = 7,
    require_safe_start: bool = True,
) -> OptimisationResult:
    """Coordinate descent over every numeric field in the protocol.

    Coordinate descent rather than a full grid because the field count grows
    with protocol length and a full sweep of a three-step protocol is thousands
    of engine runs. Two passes are enough to settle in practice, and each run is
    deterministic so the search is reproducible.
    """
    base_score, base_outcome, base_safe = score_protocol(patient, steps, params)
    baseline = Candidate(list(steps), base_outcome, base_score, base_safe)

    best = Candidate([Intervention(s.kind, dict(s.params), s.rationale) for s in steps],
                     base_outcome, base_score, base_safe)
    evaluations = 1
    rejected = 0
    notes: list[str] = []

    if require_safe_start and not base_safe:
        notes.append("the protocol as proposed reacts on its own first dose")

    # Safety is lexicographically ahead of score, not a filter applied to
    # candidates only. Filtering alone leaves an unsafe *starting* protocol
    # unbeatable — nothing may replace it, however safe — and the search then
    # reports the very protocol every reviewer objected to.
    def better(candidate: Candidate, incumbent: Candidate) -> bool:
        if require_safe_start and candidate.safe_start != incumbent.safe_start:
            return candidate.safe_start
        return candidate.score > incumbent.score + 1e-9

    for _ in range(passes):
        improved_this_pass = False
        for index, step in enumerate(best.steps):
            lever = LEVERS[step.kind]
            for name, (low, high, _unit) in lever.fields.items():
                for value in _grid(low, high, step.params[name], points):
                    if value == best.steps[index].params[name]:
                        continue
                    trial = [Intervention(s.kind, dict(s.params), s.rationale)
                             for s in best.steps]
                    trial[index].params[name] = value
                    try:
                        score, outcome, safe = score_protocol(patient, trial, params)
                    except Exception:
                        continue
                    evaluations += 1
                    if require_safe_start and not safe:
                        rejected += 1
                        continue
                    candidate = Candidate(trial, outcome, score, safe)
                    if better(candidate, best):
                        best = candidate
                        improved_this_pass = True
        if not improved_this_pass:
            break

    if rejected:
        notes.append(f"{rejected} candidate settings were discarded for starting "
                     f"above the patient's own eliciting dose")
    if require_safe_start and not best.safe_start:
        notes.append("no setting on this grid could be given safely from day one — "
                     "a real protocol would have to build the dose up gradually, "
                     "which this model does not simulate")
    elif require_safe_start and not baseline.safe_start:
        notes.append("the proposed protocol reacted on its own first dose; "
                     "the optimised one does not")

    return OptimisationResult(baseline=baseline, best=best,
                              evaluations=evaluations, rejected_unsafe=rejected,
                              notes=notes)


def summarise(result: OptimisationResult) -> str:
    before, after = result.baseline.outcome, result.best.outcome
    lines = [
        f"searched {result.evaluations} settings",
        f"  as proposed : {result.baseline.describe()}",
        f"                threshold {before.eliciting_dose_after_mg:.4g} mg "
        f"({before.fold_shift:.1f}x)",
        f"  optimised   : {result.best.describe()}",
        f"                threshold {after.eliciting_dose_after_mg:.4g} mg "
        f"({after.fold_shift:.1f}x)",
    ]
    if result.best.steps != result.baseline.steps:
        if math.isfinite(result.gain) and result.gain > 1.01:
            lines.append(f"  gain        : {result.gain:.1f}x higher threshold")
        elif result.best.safe_start and not result.baseline.safe_start:
            lines.append("  gain        : same protection, but now safe to start")
        else:
            lines.append("  gain        : a safer route to a comparable result")
    else:
        lines.append("  the proposed settings were already the best on this grid")
    lines.extend(f"  note        : {note}" for note in result.notes)
    return "\n".join(lines)
