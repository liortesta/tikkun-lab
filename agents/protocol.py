"""The intervention vocabulary — the only things an agent is allowed to propose.

This module is where the project's central rule is enforced structurally rather
than by asking politely. An agent cannot return "histamine fell to 3 ng/mL". It
can only return an intervention drawn from this fixed vocabulary, and the engine
then computes what that intervention does.

Every lever maps to a real, published mechanism and to a specific transform of
either the patient's immune state or a model parameter. Adding a lever means
naming the mechanism and the evidence for it — not inventing a knob.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from engine import MILK, Patient, Registry, challenge, eliciting_dose, immunotherapy

GLASS_OF_MILK_MG = 8000.0


class InterventionError(ValueError):
    """An agent proposed something outside the vocabulary or out of range."""


@dataclass(frozen=True)
class Lever:
    kind: str
    mechanism: str
    evidence: str
    fields: dict[str, tuple[float, float, str]]  # name -> (min, max, unit)


LEVERS: dict[str, Lever] = {
    "oral_immunotherapy": Lever(
        kind="oral_immunotherapy",
        mechanism="Daily allergen dosing drives blocking IgG4 up and induces "
                  "allergen-specific regulatory T cells.",
        evidence="Skripak et al. 2008, J Allergy Clin Immunol 122:1154",
        fields={"daily_dose_mg": (0.1, 5000.0, "mg milk protein/day"),
                "days": (7.0, 1095.0, "days")},
    ),
    "anti_ige": Lever(
        kind="anti_ige",
        mechanism="Omalizumab sequesters free IgE, so FcepsilonRI occupancy and "
                  "receptor number both fall and fewer receptors carry milk-specific IgE.",
        evidence="Wood et al. 2016, J Allergy Clin Immunol 137:1103 "
                 "(omalizumab alongside milk OIT)",
        fields={"free_ige_reduction": (0.0, 0.99, "fraction of free IgE removed")},
    ),
    "passive_igg4": Lever(
        kind="passive_igg4",
        mechanism="Transferred blocking antibody covers allergen epitopes directly, "
                  "without waiting for the patient to make it.",
        evidence="Orengo et al. 2018, Nat Commun 9:1421 (blocking mAbs against Ara h 2)",
        fields={"titre_mg_l": (0.0, 500.0, "mg/L milk-specific IgG4")},
    ),
    "barrier_repair": Lever(
        kind="barrier_repair",
        mechanism="Tightening the epithelial barrier lets less intact allergen reach "
                  "mucosal mast cells at the same ingested dose.",
        evidence="Niggemann & Beyer 2014, Allergy 69:1582 (co-factors and thresholds)",
        fields={"permeability_factor": (0.05, 1.0, "multiplier on allergen delivery")},
    ),
    "mast_cell_stabiliser": Lever(
        kind="mast_cell_stabiliser",
        mechanism="Cromolyn-class agents raise the aggregation needed before a mast "
                  "cell degranulates, without touching IgE or allergen.",
        evidence="Zur et al. 1987, J Allergy Clin Immunol 79:657",
        fields={"threshold_factor": (1.0, 50.0, "multiplier on crosslink threshold")},
    ),
}


@dataclass(frozen=True)
class Intervention:
    kind: str
    params: dict[str, float]
    rationale: str = ""

    def __post_init__(self):
        lever = LEVERS.get(self.kind)
        if lever is None:
            raise InterventionError(
                f"unknown intervention {self.kind!r}. "
                f"Choose from: {', '.join(sorted(LEVERS))}")
        for name, value in self.params.items():
            if name not in lever.fields:
                raise InterventionError(
                    f"{self.kind} has no parameter {name!r}. "
                    f"Expected: {', '.join(lever.fields)}")
            low, high, unit = lever.fields[name]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise InterventionError(f"{self.kind}.{name} must be a number")
            if not math.isfinite(value) or not low <= value <= high:
                raise InterventionError(
                    f"{self.kind}.{name} = {value} is outside {low}-{high} {unit}")
        missing = set(lever.fields) - set(self.params)
        if missing:
            raise InterventionError(
                f"{self.kind} is missing {', '.join(sorted(missing))}")

    def describe(self) -> str:
        args = ", ".join(f"{k}={v:g}" for k, v in sorted(self.params.items()))
        return f"{self.kind}({args})"

    @classmethod
    def from_dict(cls, raw: Any) -> "Intervention":
        if not isinstance(raw, dict):
            raise InterventionError(f"expected an object, got {type(raw).__name__}")
        kind = raw.get("kind")
        if not isinstance(kind, str):
            raise InterventionError("intervention is missing a string 'kind'")
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise InterventionError("'params' must be an object")
        return cls(kind=kind, params=dict(params),
                   rationale=str(raw.get("rationale", "")))


def apply_intervention(
    intervention: Intervention, patient: Patient, params: Registry
) -> tuple[Patient, Registry]:
    """Run one lever. Returns the resulting patient and parameter set."""
    kind, values = intervention.kind, intervention.params

    if kind == "oral_immunotherapy":
        course = immunotherapy(patient, values["daily_dose_mg"], values["days"], params)
        return course.final, params

    if kind == "anti_ige":
        keep = 1.0 - values["free_ige_reduction"]
        return replace(patient,
                       specific_ige_ku=patient.specific_ige_ku * keep,
                       total_ige_ku=patient.total_ige_ku * keep), params

    if kind == "passive_igg4":
        molar = values["titre_mg_l"] * 1e-3 / params.igg4_mw
        # Passive transfer adds to whatever the patient already makes.
        return replace(patient, igg4_m=patient.igg4_m + molar), params

    if kind == "barrier_repair":
        return replace(patient,
                       mucosal_barrier=patient.mucosal_barrier
                       * values["permeability_factor"]), params

    if kind == "mast_cell_stabiliser":
        return patient, params.override(
            crosslink_threshold=params.crosslink_threshold * values["threshold_factor"])

    raise InterventionError(f"no handler for {kind!r}")


@dataclass
class Outcome:
    """What the engine measured. Every number in a report must come from here."""

    label: str
    interventions: list[Intervention] = field(default_factory=list)
    eliciting_dose_before_mg: float = 0.0
    eliciting_dose_after_mg: float = 0.0
    fold_shift: float = 1.0
    glass_score_before: float = 0.0
    glass_score_after: float = 0.0
    specific_ige_ku: float = 0.0
    igg4_mg_l: float = 0.0
    treg: float = 0.0
    mucosal_barrier: float = 1.0
    protects_against_a_glass: bool = False

    def as_table(self) -> str:
        before, after = self.eliciting_dose_before_mg, self.eliciting_dose_after_mg
        shift = "no reaction at any dose" if not math.isfinite(after) \
            else f"{self.fold_shift:.1f}x"
        return "\n".join([
            f"protocol            {self.label}",
            f"steps               {'; '.join(i.describe() for i in self.interventions) or 'none'}",
            f"eliciting dose      {before:.4g} -> {after:.4g} mg milk protein ({shift})",
            f"glass of milk       severity {self.glass_score_before:.2f} -> "
            f"{self.glass_score_after:.2f} / 10",
            f"milk-specific IgE   {self.specific_ige_ku:.2f} kU/L",
            f"blocking IgG4       {self.igg4_mg_l:.1f} mg/L",
            f"regulatory T cells  {self.treg:.2f}",
            f"mucosal barrier     {self.mucosal_barrier:.2f}x",
            f"protects a glass    {'yes' if self.protects_against_a_glass else 'no'}",
        ])

    def numbers(self) -> list[float]:
        """Every value a report is allowed to quote. Used by the guard."""
        return [self.eliciting_dose_before_mg, self.eliciting_dose_after_mg,
                self.fold_shift, self.glass_score_before, self.glass_score_after,
                self.specific_ige_ku, self.igg4_mg_l, self.treg, self.mucosal_barrier,
                *(v for i in self.interventions for v in i.params.values())]


def run_protocol(
    patient: Patient, interventions: list[Intervention], label: str = "protocol",
    params: Registry = MILK,
) -> Outcome:
    """Apply interventions in order and measure the result. Fully deterministic."""
    before = eliciting_dose(patient, params)
    glass_before = challenge(patient, GLASS_OF_MILK_MG, params).symptom_score

    current, current_params = patient, params
    for step in interventions:
        current, current_params = apply_intervention(step, current, current_params)

    after = eliciting_dose(current, current_params)
    glass_after = challenge(current, GLASS_OF_MILK_MG, current_params)

    return Outcome(
        label=label,
        interventions=list(interventions),
        eliciting_dose_before_mg=before,
        eliciting_dose_after_mg=after,
        fold_shift=(after / before) if before > 0 else math.inf,
        glass_score_before=glass_before,
        glass_score_after=glass_after.symptom_score,
        specific_ige_ku=current.specific_ige_ku,
        igg4_mg_l=current.igg4_m * current_params.igg4_mw * 1e3,
        treg=current.treg,
        mucosal_barrier=current.mucosal_barrier,
        protects_against_a_glass=not glass_after.reaction,
    )


def vocabulary_prompt() -> str:
    """The lever list, formatted for an agent prompt."""
    lines = []
    for lever in LEVERS.values():
        args = ", ".join(f"{n} ({lo:g}-{hi:g} {unit})"
                         for n, (lo, hi, unit) in lever.fields.items())
        lines.append(f"- {lever.kind}: {args}\n"
                     f"    mechanism: {lever.mechanism}\n"
                     f"    evidence:  {lever.evidence}")
    return "\n".join(lines)
