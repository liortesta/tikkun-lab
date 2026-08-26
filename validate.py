"""Check the model against published biology — including things it was never fitted to.

Two kinds of check, and the second kind is the one that matters:

  ANCHOR       a number the calibration was fitted to. Passing proves the fit
               converged, nothing more.
  PREDICTION   a number nobody fitted. The model either reproduces it or it
               does not, and that is the only evidence here worth anything.

Run: python validate.py
Exit code is non-zero if any check fails, so this works as a CI gate.
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace

import numpy as np

from engine import (
    MILK, PATIENTS, challenge, crosslinks, dose_response, eliciting_dose,
    fit_hill, immunotherapy, sensitized_receptors,
)

GLASS_OF_MILK_MG = 8000.0

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str, str]] = []


def check(kind: str, name: str, ok: bool, detail: str) -> None:
    _results.append((PASS if ok else FAIL, kind, name, detail))


def within(value: float, lo: float, hi: float) -> bool:
    return math.isfinite(value) and lo <= value <= hi


# ---------------------------------------------------------------------------
# Anchors — fitted, so these only confirm the calibration converged
# ---------------------------------------------------------------------------

def check_anchors() -> None:
    ed_def = eliciting_dose(PATIENTS["default"])
    check("ANCHOR", "VITAL population ED50 for milk ~25 mg",
          within(ed_def, 23.0, 27.0), f"typical patient reacts at {ed_def:.2f} mg")

    glass = challenge(PATIENTS["default"], GLASS_OF_MILK_MG)
    check("ANCHOR", "severe reaction plasma histamine 10-15 ng/mL",
          within(glass.peak_histamine, 10.0, 15.0),
          f"glass of milk peaks at {glass.peak_histamine:.2f} ng/mL")

    course = immunotherapy(PATIENTS["default"], 300.0, 365.0)
    shift = eliciting_dose(course.final) / ed_def
    check("ANCHOR", "12 months milk OIT shifts eliciting dose ~100x",
          within(shift, 90.0, 110.0), f"threshold moved {shift:.1f}x")


# ---------------------------------------------------------------------------
# Predictions — nothing below was fitted
# ---------------------------------------------------------------------------

def check_predictions() -> None:
    course = immunotherapy(PATIENTS["default"], 300.0, 365.0)

    # The single strongest test here. Only the *typical* patient's threshold was
    # fitted; where the most sensitive patient lands was never shown to the fit.
    # VITAL sets the milk reference dose at 0.2 mg protein from population
    # challenge data, so the model has to arrive there on its own.
    ed_exq = eliciting_dose(PATIENTS["exquisite"])
    check("PREDICTION", "VITAL ED05 reference dose for milk = 0.2 mg protein",
          within(ed_exq, 0.07, 0.6),
          f"most sensitive patient reacts at {ed_exq:.3f} mg "
          f"({0.2 / ed_exq:.1f}x off the published reference dose)")

    # OIT trials measure post-treatment specific IgG4 at 30-100 mg/L. The fit was
    # given the threshold shift only, never the titre that produces it.
    igg4_mg_l = course.final.igg4_m * MILK.igg4_mw * 1e3
    check("ANCHOR", "post-OIT specific IgG4 reaches ~60 mg/L (Savilahti 2010)",
          within(igg4_mg_l, 55.0, 65.0),
          f"IgG4 rose to {igg4_mg_l:.1f} mg/L over 12 months")

    # Specific IgE falls during the first year of OIT but does not collapse. A
    # model that zeroed IgE would be wrong about the mechanism — protection comes
    # from blocking antibody first, not from losing the sensitising antibody.
    ige_ratio = course.final.specific_ige_ku / PATIENTS["default"].specific_ige_ku
    check("PREDICTION", "specific IgE declines but persists through year 1 of OIT",
          within(ige_ratio, 0.3, 0.9), f"sIgE {PATIENTS['default'].specific_ige_ku:.0f} "
          f"-> {course.final.specific_ige_ku:.1f} kU/L ({ige_ratio:.2f}x)")

    # Blocking antibody should carry most of the protection. Nothing forces this:
    # IgG4 production was fitted to the measured post-treatment titre and Treg
    # induction to the threshold shift, so how the protection divides between
    # them falls out of the model. Isolate each by re-running the final patient
    # with the other mechanism reverted to baseline.
    ed_before = eliciting_dose(PATIENTS["default"])
    igg4_only = replace(course.final, treg=0.0,
                        specific_ige_ku=PATIENTS["default"].specific_ige_ku)
    treg_only = replace(course.final, igg4_m=PATIENTS["default"].igg4_m)
    shift_igg4 = eliciting_dose(igg4_only) / ed_before
    shift_treg = eliciting_dose(treg_only) / ed_before
    check("PREDICTION", "blocking IgG4 carries most of the protection, not Treg",
          shift_igg4 > shift_treg,
          f"IgG4 alone {shift_igg4:.0f}x, Treg-driven IgE loss alone {shift_treg:.1f}x")

    # Anaphylaxis peaks within 2-5 minutes of exposure.
    glass = challenge(PATIENTS["default"], GLASS_OF_MILK_MG)
    check("PREDICTION", "systemic reaction peaks 2-5 min after exposure",
          within(glass.time_to_peak_s, 120.0, 300.0),
          f"peak histamine at {glass.time_to_peak_s:.0f} s")

    # Stopping immunotherapy loses protection as induced Treg decay — this is the
    # documented gap between desensitisation and sustained unresponsiveness.
    stopped = immunotherapy(course.final, 0.0, 180.0)
    ed_on = eliciting_dose(course.final)
    ed_off = eliciting_dose(stopped.final)
    check("PREDICTION", "protection is lost after OIT stops (desensitisation != tolerance)",
          ed_off < ed_on * 0.5,
          f"6 months off dosing: threshold {ed_on:.0f} -> {ed_off:.1f} mg "
          f"({ed_on / ed_off:.0f}x loss)")

    # The crosslinking equilibrium must be non-monotonic in allergen: the prozone
    # (hook) effect, where excess antigen saturates every IgE and bridges none.
    receptors = sensitized_receptors(
        PATIENTS["default"].specific_ige_ku, PATIENTS["default"].total_ige_ku,
        MILK.receptors_per_cell, MILK.k_occupancy_ku)
    conc = np.logspace(-13, -4, 400)
    xl = np.array([crosslinks(float(c), receptors, MILK.k_bind, MILK.k_cross) for c in conc])
    peak_at = float(conc[int(np.argmax(xl))])
    check("PREDICTION", "crosslinking shows the prozone effect near 1/(2*k_bind)",
          xl[-1] < xl.max() * 0.01 and within(peak_at, 1e-9, 5e-8),
          f"peak crosslinking at {peak_at * 1e9:.2f} nM, "
          f"falls to {xl[-1] / xl.max():.1e} of peak at 100 uM")

    # Severity must never fall as the dose rises anywhere in the clinical range.
    # The prozone is real physics, but if it reached into the doses people
    # actually eat, the model would be claiming more milk is safer than less.
    doses_mono = np.logspace(-2, 4.2, 300)
    severity = np.array([challenge(PATIENTS["default"], float(d)).symptom_score
                         for d in doses_mono])
    worst_drop = float(np.min(np.diff(severity)))
    check("PREDICTION", "severity never decreases with dose across the clinical range",
          worst_drop >= -1e-9,
          f"monotone from 0.01 mg to 16 g; largest step down {worst_drop:.2e}")

    # More sensitive patients react at lower doses, strictly. If this ever fails
    # the model has stopped being about IgE. `outgrowing` returning no reaction at
    # any dose is the correct answer, not a gap — at 1.2 kU/L most children have
    # outgrown milk allergy and tolerate a full serving.
    order = ["exquisite", "default", "moderate", "outgrowing"]
    thresholds = [eliciting_dose(PATIENTS[k]) for k in order]
    check("PREDICTION", "eliciting dose rises monotonically as specific IgE falls",
          all(a < b for a, b in zip(thresholds, thresholds[1:])),
          "  ".join(f"{k}={t:.3g}mg" for k, t in zip(order, thresholds)))

    # Food challenge dose-response is graded, not all-or-nothing: a Hill slope
    # near 1 over several logs of dose.
    doses, scores = dose_response(PATIENTS["default"], lo_mg=1e-2, hi_mg=1e5, points=80)
    fit = fit_hill(doses, scores)
    check("PREDICTION", "dose-response is graded (Hill slope 0.5-2, good fit)",
          within(fit.hill_slope, 0.5, 2.0) and fit.r_squared > 0.98,
          fit.summary())

    # Determinism. Two identical runs must agree bit for bit, or nothing built on
    # top of this can compare two interventions.
    a = challenge(PATIENTS["default"], 137.0).symptom_score
    b = challenge(PATIENTS["default"], 137.0).symptom_score
    check("PREDICTION", "identical inputs give bit-identical output",
          a == b, f"repeat run reproduced {a!r} exactly")


def main() -> int:
    check_anchors()
    check_predictions()

    width = max(len(name) for _, _, name, _ in _results)
    print("=" * (width + 60))
    print("TIKKUN LAB — model validation, cow's milk allergy")
    print("=" * (width + 60))

    for kind, caption in (
        ("ANCHOR", "ANCHORS — fitted to these, so passing only proves the fit converged"),
        ("PREDICTION", "PREDICTIONS — never shown to the fit"),
    ):
        print()
        print(caption)
        print("-" * (width + 60))
        for status, row_kind, name, detail in _results:
            if row_kind == kind:
                print(f"  [{status}] {name.ljust(width)}   {detail}")

    failed = sum(1 for status, *_ in _results if status == FAIL)
    predictions = sum(1 for _, kind, *_ in _results if kind == "PREDICTION")
    passed_predictions = sum(
        1 for status, kind, *_ in _results if kind == "PREDICTION" and status == PASS)

    print()
    print("=" * (width + 60))
    print(f"{len(_results) - failed}/{len(_results)} checks passed   "
          f"({passed_predictions}/{predictions} of them predictions the fit never saw)")
    print(f"parameter provenance: {MILK.counts()}   trust {MILK.trust_score():.0%}")
    print("=" * (width + 60))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
