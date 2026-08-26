"""Fit the CALIBRATED parameters to published clinical endpoints.

Run this, then paste the results into `engine/params_milk.py`. Keeping it as a
separate, re-runnable step is the point: it makes visible exactly which numbers
were fitted, to what, and how well — instead of burying tuned constants in the
model where they look like measurements.

Anchors (all published, all cited in params_milk.py):

  A1  An exquisitely sensitive patient reacts at 0.2 mg milk protein
      (VITAL 3.0 reference dose for milk, ED05)
  A2  A typical milk-allergic patient reacts at 25 mg milk protein
      (VITAL 3.0 population ED50 range for milk)
  A3  A full glass of milk in a typical patient drives plasma histamine to
      ~12 ng/mL, the severe-reaction level (Kaliner 1982)
  A4  12 months of milk OIT at 300 mg/day raises the eliciting dose ~100-fold
      (Skripak 2008, Longo 2008)
  A5  Post-treatment milk-specific IgG4 reaches ~60 mg/L
      (Savilahti 2010, J Allergy Clin Immunol 125:1315)

Anchors are expressed as "the symptom score at this dose equals the reaction
threshold" rather than "the bisected eliciting dose equals this value". The two
statements are equivalent, but the first is smooth in the parameters while the
second is a step function that flattens the optimiser's gradient to zero.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq, least_squares

from engine import MILK, PATIENTS, challenge, eliciting_dose, immunotherapy

GLASS_OF_MILK_MG = 8000.0  # ~240 mL cow's milk at ~33 g protein/L

ANCHORS = {
    "A1_exquisite_ed_mg": 0.2,
    "A2_default_ed_mg": 25.0,
    "A3_glass_histamine_ng_ml": 12.0,
    "A4_oit_fold_shift": 100.0,
    "A5_post_oit_igg4_mg_l": 60.0,
}


def fit_fast_response(verbose: bool = True) -> dict[str, float]:
    """Fit mucosal_cmax and histamine_yield jointly to A2 and A3.

    Jointly, because the two are coupled: raising histamine_yield adds a systemic
    contribution at the A2 anchor dose and pulls that eliciting dose down.

    A1 is deliberately *not* fitted. Two free parameters are enough to place the
    typical patient's threshold and the severe-reaction histamine level, and once
    those are set, where the most sensitive patient lands is something the model
    either gets right or does not. Spending a third parameter to force it would
    destroy the only independent test of the fast timescale.

    crosslink_threshold and mucosal_km are not fitted either — the first is
    degenerate with mucosal_cmax (crosslink count only ever enters as a ratio
    against it) and nothing in these anchors identifies the second.
    """
    target = MILK.reaction_threshold

    def residuals(theta):
        p = MILK.override(mucosal_cmax=10.0 ** theta[0], histamine_yield=10.0 ** theta[1])
        default = challenge(PATIENTS["default"], ANCHORS["A2_default_ed_mg"], p)
        glass = challenge(PATIENTS["default"], GLASS_OF_MILK_MG, p)
        return [
            (default.symptom_score - target) / target,
            (glass.peak_histamine - ANCHORS["A3_glass_histamine_ng_ml"])
            / ANCHORS["A3_glass_histamine_ng_ml"],
        ]

    start = [math.log10(MILK.mucosal_cmax), math.log10(MILK.histamine_yield)]
    result = least_squares(residuals, start, bounds=([-13.0, 0.0], [-7.0, 3.5]),
                           xtol=1e-13, ftol=1e-13, gtol=1e-13)
    fitted = {"mucosal_cmax": 10.0 ** result.x[0], "histamine_yield": 10.0 ** result.x[1]}

    if verbose:
        p = MILK.override(**fitted)
        print("  mucosal_cmax = %.4g M   histamine_yield = %.4g ng/mL"
              % (fitted["mucosal_cmax"], fitted["histamine_yield"]))
        print("    residuals %.2e / %.2e" % tuple(result.fun))
        print("    default    ED = %8.3f mg   (anchor %.3f)"
              % (eliciting_dose(PATIENTS["default"], p), ANCHORS["A2_default_ed_mg"]))
        glass = challenge(PATIENTS["default"], GLASS_OF_MILK_MG, p)
        print("    glass of milk -> peak histamine %.2f ng/mL at %.0f s, symptom score %.2f"
              % (glass.peak_histamine, glass.time_to_peak_s, glass.symptom_score))
        print("  not fitted, left as tests:")
        for name in ("exquisite", "moderate", "outgrowing"):
            ed = eliciting_dose(PATIENTS[name], p)
            note = "   <- VITAL ED05 is 0.2 mg" if name == "exquisite" else ""
            print("    %-10s ED = %8.3f mg%s" % (name, ed, note))
    return fitted


def fit_immunotherapy(base: dict[str, float], verbose: bool = True) -> dict[str, float]:
    """Fit igg4_kprod to A5 and treg_kind to A4, in that order.

    Sequential and not joint, because A5 pins IgG4 production on its own: the
    post-treatment titre is directly measured, so it should never be inferred
    from the threshold shift. Treg induction then picks up whatever protection
    the blocking antibody does not account for. Which of the two mechanisms ends
    up dominant is then a result, not an input.
    """
    p_base = MILK.override(**base)
    ed_before = eliciting_dose(PATIENTS["default"], p_base)

    # Both fits are root-found rather than least-squares fitted. Each target is
    # strictly monotone in its parameter, so a bracketed root-finder is exact and
    # immune to the two traps that caught earlier versions here: the bisected
    # eliciting dose is a step function with no usable gradient, and the symptom
    # score at a fixed high dose saturates at the top of the 0-10 scale, which
    # flattens the residual and leaves a gradient optimiser sitting at its start.

    # A5 identifies IgG4 production on its own — the post-treatment titre is a
    # directly measured quantity, so it should not be inferred from the threshold
    # shift. An earlier version scaled IgG4 and Treg together against A4 alone,
    # which left them unidentified and drove Treg induction implausibly fast.
    def igg4_gap(log_kprod):
        p = MILK.override(igg4_kprod=10.0**log_kprod, **base)
        course = immunotherapy(PATIENTS["default"], 300.0, 365.0, p)
        return course.final.igg4_m * MILK.igg4_mw * 1e3 - ANCHORS["A5_post_oit_igg4_mg_l"]

    igg4_kprod = 10.0 ** brentq(igg4_gap, -10.0, -5.0, xtol=1e-12)

    def treg_gap(log_kind):
        p = MILK.override(igg4_kprod=igg4_kprod, treg_kind=10.0**log_kind, **base)
        course = immunotherapy(PATIENTS["default"], 300.0, 365.0, p)
        after = eliciting_dose(course.final, p)
        return math.log10(after / ed_before) - math.log10(ANCHORS["A4_oit_fold_shift"])

    treg_kind = 10.0 ** brentq(treg_gap, -4.0, -0.3, xtol=1e-10)
    fitted = {"igg4_kprod": igg4_kprod, "treg_kind": treg_kind}

    if verbose:
        p = MILK.override(**fitted, **base)
        course = immunotherapy(PATIENTS["default"], 300.0, 365.0, p)
        ed_after = eliciting_dose(course.final, p)
        igg4_mg_l = course.final.igg4_m * MILK.igg4_mw * 1e3
        print("  igg4_kprod = %.4g M/day    treg_kind = %.4g 1/day"
              % (fitted["igg4_kprod"], fitted["treg_kind"]))
        print("    12 months OIT at 300 mg/day: ED %.3f -> %.3f mg (%.1fx, anchor %.0fx)"
              % (ed_before, ed_after, ed_after / ed_before, ANCHORS["A4_oit_fold_shift"]))
        print("    final sIgG4 %.1f nM = %.1f mg/L (%.0fx baseline), Treg %.2f, sIgE %.1f kU/L"
              % (course.final.igg4_m * 1e9, igg4_mg_l,
                 course.final.igg4_m / PATIENTS["default"].igg4_m,
                 course.final.treg, course.final.specific_ige_ku))
    return fitted


def main() -> dict[str, float]:
    print("A1 + A2 + A3  fast dose-response and severity scale")
    fast = fit_fast_response()

    print("\nA4  immunotherapy rates")
    slow = fit_immunotherapy(fast)

    fitted = {**fast, **slow}
    print("\n" + "=" * 68)
    print("Paste into engine/params_milk.py:")
    print("=" * 68)
    for key, value in fitted.items():
        print(f"  {key:<22} {value:.6g}")
    return fitted


if __name__ == "__main__":
    main()
