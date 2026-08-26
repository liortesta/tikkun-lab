"""Tikkun Lab — run experiments from the terminal.

    python cli.py patients                       list the built-in patients
    python cli.py challenge --dose 25            one oral food challenge
    python cli.py threshold                      eliciting dose for every patient
    python cli.py curve --patient default        dose-response curve with EC50
    python cli.py oit --dose 300 --days 365      a course of immunotherapy
    python cli.py trial                          OIT dose-finding across a grid
    python cli.py audit                          provenance of every parameter

Add `--json` to any command to get machine-readable output instead of a table.
"""

from __future__ import annotations

import argparse
import json
import math
import sys

from engine import (
    MILK, PATIENTS, Patient, challenge, dose_response, eliciting_dose,
    fit_hill, immunotherapy, summarise_shift,
)

BAR = "=" * 78
GLASS_OF_MILK_MG = 8000.0


def resolve_patient(name: str) -> Patient:
    if name not in PATIENTS:
        raise SystemExit(f"unknown patient {name!r}. Choose from: {', '.join(PATIENTS)}")
    return PATIENTS[name]


def sparkline(values, width: int = 48) -> str:
    """A quick visual for a curve, so a shape is visible without a plot window."""
    blocks = " .:-=+*#%@"
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    step = max(1, len(values) // width)
    return "".join(blocks[min(9, int((v - lo) / span * 9.999))] for v in values[::step])


# ---------------------------------------------------------------------------

def cmd_patients(args) -> None:
    if args.json:
        print(json.dumps({k: vars(v) for k, v in PATIENTS.items()}, indent=2))
        return
    print(BAR)
    print("Built-in patients")
    print(BAR)
    for patient in PATIENTS.values():
        ed = eliciting_dose(patient)
        shown = "no reaction at any dose" if not math.isfinite(ed) else f"{ed:.3g} mg"
        print(f"  {patient.describe()}")
        print(f"      eliciting dose: {shown}")


def cmd_challenge(args) -> None:
    patient = resolve_patient(args.patient)
    result = challenge(patient, args.dose)
    if args.json:
        print(json.dumps({k: v for k, v in vars(result).items()
                          if not k.startswith("trace")}, indent=2))
        return

    print(BAR)
    print(f"Oral food challenge — {args.dose:g} mg cow's milk protein")
    print(BAR)
    print(f"  {patient.describe()}")
    print()
    print(f"  allergen at the mast cell    {result.free_allergen_m * 1e9:12.4f} nM")
    print(f"  receptor crosslinks / cell   {result.crosslinks_per_cell:12.1f}"
          f"   (threshold {MILK.crosslink_threshold:.0f})")
    print(f"  mast cells degranulating     {result.activation:12.1%}")
    print(f"  mast cell pool engaged       {result.engaged_fraction:12.1%}")
    print()
    print(f"  peak plasma histamine        {result.peak_histamine:12.2f} ng/mL"
          f"   at {result.time_to_peak_s / 60:.1f} min")
    print(f"  local / GI symptoms          {result.local_score:12.2f} / 10")
    print(f"  systemic symptoms            {result.systemic_score:12.2f} / 10")
    print(f"  overall severity             {result.symptom_score:12.2f} / 10")
    print()
    verdict = "REACTION — objective symptoms" if result.reaction else "tolerated"
    print(f"  {verdict}")


def cmd_threshold(args) -> None:
    rows = {}
    for name, patient in PATIENTS.items():
        ed = eliciting_dose(patient)
        rows[name] = ed
    if args.json:
        print(json.dumps(rows, indent=2))
        return

    print(BAR)
    print("Eliciting dose — smallest dose producing objective symptoms")
    print(BAR)
    print(f"  {'patient':<12} {'milk-sIgE':>12} {'eliciting dose':>16}  in practice")
    print(f"  {'-' * 12} {'-' * 12} {'-' * 16}  {'-' * 26}")
    for name, ed in rows.items():
        patient = PATIENTS[name]
        if not math.isfinite(ed):
            shown, practical = "no reaction", "tolerates a full serving"
        else:
            shown = f"{ed:.3g} mg"
            millilitres = ed / 33.0  # cow's milk carries ~33 mg protein per mL
            if millilitres < 0.05:
                practical = f"{millilitres * 1000:.0f} uL — less than one drop"
            elif millilitres < 1.0:
                practical = f"{millilitres * 1000:.0f} uL of milk"
            elif millilitres < 240.0:
                practical = f"{millilitres:.1f} mL of milk"
            else:
                practical = "a full glass or more"
        sige = f"{patient.specific_ige_ku:.1f} kU/L"
        print(f"  {name:<12} {sige:>12} {shown:>16}  {practical}")
    print()
    print(f"  For reference, VITAL sets the milk reference dose at 0.2 mg protein,")
    print(f"  and a 240 mL glass of milk carries about {GLASS_OF_MILK_MG:.0f} mg.")


def cmd_curve(args) -> None:
    patient = resolve_patient(args.patient)
    doses, scores = dose_response(patient, lo_mg=args.lo, hi_mg=args.hi, points=args.points)
    fit = fit_hill(doses, scores)
    if args.json:
        print(json.dumps({"doses": doses.tolist(), "scores": scores.tolist(),
                          "ec50": fit.ec50, "hill_slope": fit.hill_slope,
                          "r_squared": fit.r_squared}, indent=2))
        return

    print(BAR)
    print(f"Dose-response — {patient.label}")
    print(BAR)
    print(f"  {sparkline(list(scores))}")
    print(f"  {args.lo:<.3g} mg" + " " * 34 + f"{args.hi:.3g} mg")
    print()
    print(f"  EC50            {fit.ec50:10.3g} mg milk protein")
    print(f"  ED10            {fit.effective_dose(0.10):10.3g} mg")
    print(f"  ED90            {fit.effective_dose(0.90):10.3g} mg")
    print(f"  Hill slope      {fit.hill_slope:10.2f}")
    print(f"  fit quality     {fit.r_squared:10.4f} R^2")
    print()
    print(f"  eliciting dose  {eliciting_dose(patient):10.3g} mg   (objective symptoms)")


def cmd_oit(args) -> None:
    patient = resolve_patient(args.patient)
    before = eliciting_dose(patient)
    course = immunotherapy(patient, args.dose, args.days)
    after = eliciting_dose(course.final)

    if args.json:
        print(json.dumps({
            "daily_dose_mg": args.dose, "days": args.days,
            "eliciting_dose_before_mg": before, "eliciting_dose_after_mg": after,
            "specific_ige_ku": course.final.specific_ige_ku,
            "igg4_mg_l": course.final.igg4_m * MILK.igg4_mw * 1e3,
            "treg": course.final.treg,
        }, indent=2))
        return

    print(BAR)
    print(f"Oral immunotherapy — {args.dose:g} mg/day for {args.days:g} days")
    print(BAR)
    print(f"  {patient.describe()}")
    print()
    print(f"  {'':<22} {'before':>12} {'after':>12}")
    print(f"  {'-' * 22} {'-' * 12} {'-' * 12}")
    print(f"  {'milk-specific IgE':<22} {patient.specific_ige_ku:>9.1f} kU/L"
          f" {course.final.specific_ige_ku:>9.1f} kU/L")
    print(f"  {'blocking IgG4':<22} {patient.igg4_m * MILK.igg4_mw * 1e3:>9.1f} mg/L"
          f" {course.final.igg4_m * MILK.igg4_mw * 1e3:>9.1f} mg/L")
    print(f"  {'regulatory T cells':<22} {patient.treg:>12.2f} {course.final.treg:>12.2f}")
    print()
    print(f"  eliciting dose   {summarise_shift(before, after)}")
    print()
    glass = challenge(course.final, GLASS_OF_MILK_MG)
    print(f"  a full glass of milk now scores {glass.symptom_score:.1f}/10 "
          f"({'still a reaction' if glass.reaction else 'tolerated'})")


def cmd_trial(args) -> None:
    """Sweep maintenance dose — the question a protocol designer actually asks."""
    patient = resolve_patient(args.patient)
    before = eliciting_dose(patient)
    doses = [3.0, 10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0]
    rows = []
    for daily in doses:
        course = immunotherapy(patient, daily, args.days)
        after = eliciting_dose(course.final)
        reactions = challenge(patient, daily).reaction  # would the first dose react?
        rows.append((daily, after, after / before, reactions,
                     course.final.igg4_m * MILK.igg4_mw * 1e3))

    if args.json:
        print(json.dumps([{"daily_dose_mg": d, "eliciting_dose_after_mg": a,
                           "fold_shift": f, "first_dose_reacts": r, "igg4_mg_l": g}
                          for d, a, f, r, g in rows], indent=2))
        return

    print(BAR)
    print(f"Maintenance dose sweep — {patient.label}, {args.days:g} days")
    print(BAR)
    print(f"  baseline eliciting dose {before:.3g} mg")
    print()
    print(f"  {'daily dose':>12} {'final threshold':>17} {'shift':>9} {'IgG4':>10}  safety")
    print(f"  {'-' * 12} {'-' * 17} {'-' * 9} {'-' * 10}  {'-' * 26}")
    for daily, after, fold, reacts, igg4 in rows:
        safety = "first dose reacts — needs updosing" if reacts else "starting dose tolerated"
        print(f"  {daily:>9.0f} mg {after:>14.0f} mg {fold:>8.0f}x {igg4:>7.1f} mg/L  {safety}")
    print()
    print("  Higher maintenance doses protect more but start above the patient's")
    print("  threshold, which is why real protocols build up instead of starting there.")


def cmd_lab(args) -> None:
    """The agent army: design protocols, simulate them, review, judge."""
    from agents import Lab, render

    patient = resolve_patient(args.patient)
    lab = Lab(patient, offline=args.offline, pi_model=args.pi_model,
              review_model=args.review_model, judge_model=args.judge_model)
    session = lab.run(args.goal, count=args.count)

    if args.json:
        print(json.dumps({
            "goal": session.goal,
            "patient": vars(session.patient),
            "outcomes": [{**{k: v for k, v in vars(o).items() if k != "interventions"},
                          "steps": [i.describe() for i in o.interventions]}
                         for o in session.outcomes],
            "reviews": [{"role": r.role, "model": r.model, "text": r.text,
                         "untraceable": [c.value for c in r.untraceable]}
                        for r in session.reviews],
            "verdict": session.verdict,
            "notes": session.notes,
        }, indent=2, default=str))
        return
    print(render(session))


def cmd_audit(args) -> None:
    if args.json:
        print(json.dumps({k: {"value": p.value, "unit": p.unit,
                              "provenance": p.provenance.value, "source": p.source,
                              "note": p.note}
                          for k, p in MILK.items()}, indent=2))
        return
    print(BAR)
    print("Parameter provenance — cow's milk allergy model")
    print(BAR)
    print(MILK.audit())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tikkun", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, fn, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(func=fn)
        return sp

    add("patients", cmd_patients, "list the built-in patients and their thresholds")

    sp = add("challenge", cmd_challenge, "run one oral food challenge")
    sp.add_argument("--patient", default="default")
    sp.add_argument("--dose", type=float, default=25.0, help="mg milk protein")

    add("threshold", cmd_threshold, "eliciting dose for every patient")

    sp = add("curve", cmd_curve, "dose-response curve with a fitted EC50")
    sp.add_argument("--patient", default="default")
    sp.add_argument("--lo", type=float, default=1e-2)
    sp.add_argument("--hi", type=float, default=1e4)
    sp.add_argument("--points", type=int, default=60)

    sp = add("oit", cmd_oit, "run a course of oral immunotherapy")
    sp.add_argument("--patient", default="default")
    sp.add_argument("--dose", type=float, default=300.0, help="mg/day maintenance")
    sp.add_argument("--days", type=float, default=365.0)

    sp = add("trial", cmd_trial, "sweep maintenance dose across a grid")
    sp.add_argument("--patient", default="default")
    sp.add_argument("--days", type=float, default=365.0)

    sp = add("lab", cmd_lab, "run the agent army on a goal")
    sp.add_argument("--patient", default="default")
    sp.add_argument("--goal", default="Raise this patient's reaction threshold "
                                      "enough that an accidental exposure is safe")
    sp.add_argument("--count", type=int, default=4, help="protocols to propose")
    sp.add_argument("--offline", action="store_true",
                    help="skip the models and use the built-in protocol panel")
    sp.add_argument("--pi-model", default="claude-fable-5", dest="pi_model")
    sp.add_argument("--review-model", default="gpt-5-6-luna", dest="review_model")
    sp.add_argument("--judge-model", default="claude-opus-4-8", dest="judge_model")

    add("audit", cmd_audit, "provenance of every parameter")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
