"""Tikkun Lab simulation engine.

The engine produces every number in this project. Language models never do —
they propose what to simulate and interpret what comes back, but the values
themselves come from here, from equilibrium binding theory and ODEs whose
parameters carry citations.
"""

from .binding import crosslinks, free_allergen, hill, sensitized_receptors
from .metrics import HillFit, fit_hill, fold_change, log_shift, summarise_shift, therapeutic_index
from .milk import (
    PATIENTS,
    ChallengeResult,
    CourseResult,
    Patient,
    challenge,
    dose_response,
    eliciting_dose,
    immunotherapy,
)
from .params_milk import MILK, milk_protein_to_blg_molar
from .provenance import Param, Provenance, Registry

__all__ = [
    "MILK", "PATIENTS", "Param", "Patient", "Provenance", "Registry",
    "ChallengeResult", "CourseResult", "HillFit",
    "challenge", "crosslinks", "dose_response", "eliciting_dose", "fit_hill",
    "fold_change", "free_allergen", "hill", "immunotherapy", "log_shift",
    "milk_protein_to_blg_molar", "sensitized_receptors", "summarise_shift",
    "therapeutic_index",
]

__version__ = "0.1.0"
