"""Generate reference values from the Python engine for the JavaScript port to match.

The browser lab cannot call Python, so the engine is ported to JavaScript. A port
that quietly drifts from the original is worse than no port at all — it would
still produce confident numbers, just different ones. This fixture pins the
Python engine's answers so `verify.mjs` can prove the two agree.

Run after any engine change:  python web/fixture.py
"""

from __future__ import annotations

import json
import math
import os

from engine import MILK, PATIENTS, challenge, eliciting_dose, immunotherapy

HERE = os.path.dirname(os.path.abspath(__file__))

DOSES = [0.05, 0.2, 1.0, 5.0, 25.0, 100.0, 500.0, 2000.0, 8000.0]
OIT_COURSES = [(30.0, 365.0), (300.0, 90.0), (300.0, 365.0), (1000.0, 730.0)]


def _clean(value: float) -> float | str:
    return "Infinity" if math.isinf(value) else value


def main() -> None:
    fixture = {
        "params": {name: param.value for name, param in MILK.items()},
        "patients": {
            name: {
                "specific_ige_ku": p.specific_ige_ku,
                "total_ige_ku": p.total_ige_ku,
                "igg4_m": p.igg4_m,
                "treg": p.treg,
                "mucosal_barrier": p.mucosal_barrier,
            }
            for name, p in PATIENTS.items()
        },
        "challenges": [],
        "eliciting_doses": {},
        "immunotherapy": [],
    }

    for name, patient in PATIENTS.items():
        for dose in DOSES:
            result = challenge(patient, dose)
            fixture["challenges"].append({
                "patient": name, "dose_mg": dose,
                "free_allergen_m": result.free_allergen_m,
                "crosslinks_per_cell": result.crosslinks_per_cell,
                "activation": result.activation,
                "engaged_fraction": result.engaged_fraction,
                "peak_histamine": result.peak_histamine,
                "time_to_peak_s": result.time_to_peak_s,
                "symptom_score": result.symptom_score,
                "reaction": result.reaction,
            })
        fixture["eliciting_doses"][name] = _clean(eliciting_dose(patient))

    for daily, days in OIT_COURSES:
        course = immunotherapy(PATIENTS["default"], daily, days)
        fixture["immunotherapy"].append({
            "daily_dose_mg": daily, "days": days,
            "specific_ige_ku": course.final.specific_ige_ku,
            "igg4_m": course.final.igg4_m,
            "treg": course.final.treg,
            "eliciting_dose_after_mg": _clean(eliciting_dose(course.final)),
        })

    path = os.path.join(HERE, "fixture.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(fixture, handle, indent=1)

    print(f"wrote {path}")
    print(f"  {len(fixture['challenges'])} challenges, "
          f"{len(fixture['eliciting_doses'])} thresholds, "
          f"{len(fixture['immunotherapy'])} immunotherapy courses")


if __name__ == "__main__":
    main()
