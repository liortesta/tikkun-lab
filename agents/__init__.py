"""The agent layer.

Agents propose interventions and interpret results. They never produce a number —
`protocol.py` restricts what they can ask for, the engine computes what happens,
and `guard.py` flags any figure in their prose that cannot be traced back.
"""

from .guard import Claim, allowed_from, audit_text
from .lab import Lab, Review, Session, render
from .optimise import (
    Candidate, OptimisationResult, optimise, score_protocol, starting_dose_is_safe,
    summarise,
)
from .protocol import (
    LEVERS, Intervention, InterventionError, Outcome, apply_intervention,
    run_protocol, vocabulary_prompt,
)

__all__ = [
    "Candidate", "Claim", "Intervention", "InterventionError", "LEVERS", "Lab",
    "OptimisationResult", "Outcome", "Review", "Session", "allowed_from",
    "apply_intervention", "audit_text", "optimise", "render", "run_protocol",
    "score_protocol", "starting_dose_is_safe", "summarise", "vocabulary_prompt",
]
