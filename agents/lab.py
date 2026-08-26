"""The agent army: propose, simulate, review, judge.

The loop is deliberately shaped so the models never touch a number:

    1. DESIGN    the principal investigator proposes candidate protocols,
                 drawn only from the intervention vocabulary in `protocol.py`
    2. SIMULATE  the engine runs every candidate — this is the only step that
                 produces values, and it is pure arithmetic
    3. REVIEW    specialists read the engine's output and judge it, each from
                 one angle: mechanism, dosing, safety
    4. JUDGE     a critic ranks the candidates and states what the evidence
                 does and does not support

Every text a model produces is passed through `guard.audit_text`, which flags any
number that cannot be traced back to what the engine actually computed.

Runs without any API key: `Lab(offline=True)` falls back to a fixed panel of
protocols and skips the review text, so the engine half stays testable and CI
does not need credentials.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from engine import MILK, PATIENTS, Patient, Registry, eliciting_dose

from . import client, guard
from .optimise import OptimisationResult, optimise
from .protocol import (
    GLASS_OF_MILK_MG, Intervention, InterventionError, Outcome, run_protocol,
    vocabulary_prompt,
)

PI_SYSTEM = """You are the principal investigator of an allergy research group.
You design intervention protocols to be tested in a deterministic simulation.

You never state results, concentrations, thresholds or outcomes. You do not know
them — the simulator has not run yet. Your entire job is to choose which
protocols are worth simulating and to say why, mechanistically."""

REVIEW_SYSTEM = """You are reviewing simulation output for an allergy research group.

Every number you may cite is already in the output given to you. Do not compute,
estimate or recall any other figure — not a half-life, not a typical titre, not a
published threshold. If a quantity you want is not in the output, say that it was
not measured. Mechanistic reasoning without numbers is what is wanted here."""

JUDGE_SYSTEM = """You are the critic for an allergy research group. You rank
candidate protocols on simulation evidence alone and you are hard to convince.

Cite only numbers present in the results given to you. Name explicitly what the
simulation did not test — an untested risk is the finding most worth reporting."""


@dataclass
class Review:
    role: str
    model: str
    text: str
    untraceable: list[guard.Claim] = field(default_factory=list)


@dataclass
class Session:
    patient: Patient
    goal: str
    candidates: list[list[Intervention]] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    verdict: str = ""
    verdict_untraceable: list[guard.Claim] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    optimisation: OptimisationResult | None = None

    def best(self) -> Outcome | None:
        """Highest fold shift that also protects against a full glass, else the
        highest fold shift. Chosen by the engine's numbers, not by the critic."""
        if not self.outcomes:
            return None
        protective = [o for o in self.outcomes if o.protects_against_a_glass]
        return max(protective or self.outcomes, key=lambda o: o.fold_shift)


#: Used when no API key is configured, and as the seed panel the PI is asked to
#: improve on. These are real strategies: OIT alone, OIT with omalizumab
#: pre-treatment, and passive blocking antibody.
FALLBACK_PANEL: list[tuple[str, list[Intervention]]] = [
    ("OIT low dose", [Intervention("oral_immunotherapy",
                                   {"daily_dose_mg": 30.0, "days": 365.0})]),
    ("OIT standard", [Intervention("oral_immunotherapy",
                                   {"daily_dose_mg": 300.0, "days": 365.0})]),
    ("OIT high dose", [Intervention("oral_immunotherapy",
                                    {"daily_dose_mg": 1000.0, "days": 365.0})]),
    ("anti-IgE then OIT", [Intervention("anti_ige", {"free_ige_reduction": 0.95}),
                           Intervention("oral_immunotherapy",
                                        {"daily_dose_mg": 300.0, "days": 365.0})]),
    ("passive blocking antibody", [Intervention("passive_igg4", {"titre_mg_l": 120.0})]),
]


class Lab:
    def __init__(self, patient: Patient = None, params: Registry = MILK,
                 offline: bool = False, pi_model: str = client.SMART,
                 review_model: str = client.MID, judge_model: str = client.JUDGE):
        self.patient = patient or PATIENTS["default"]
        self.params = params
        self.offline = offline or not client.available()
        self.pi_model = pi_model
        self.review_model = review_model
        self.judge_model = judge_model

    # -- 1. design ---------------------------------------------------------

    def _use_fallback(self, count: int) -> list[list[Intervention]]:
        panel = FALLBACK_PANEL[:count]
        self._names = [name for name, _ in panel]
        return [steps for _, steps in panel]

    def design(self, goal: str, count: int = 4) -> tuple[list[list[Intervention]], list[str]]:
        """Ask the PI for candidate protocols. Returns (candidates, notes)."""
        notes: list[str] = []
        if self.offline:
            notes.append("offline: using the built-in protocol panel")
            return self._use_fallback(count), notes

        baseline = eliciting_dose(self.patient, self.params)
        prompt = f"""<goal>
{goal}
</goal>

<patient>
{self.patient.describe()}
This patient reacts to {baseline:.4g} mg of milk protein today. A full glass of
milk carries about {GLASS_OF_MILK_MG:.0f} mg.
</patient>

<available_interventions>
{vocabulary_prompt()}
</available_interventions>

<task>
Propose {count} distinct protocols worth simulating. Each is an ordered list of
interventions from the vocabulary above — order matters, since a protocol may
pre-treat before dosing. Vary the mechanism between candidates rather than only
the numbers: a panel of five doses of the same drug tests one hypothesis, not five.
For each, say in one sentence why that mechanism might work for this patient.
</task>"""
        schema = """[
  {"name": "short protocol name",
   "hypothesis": "one sentence on the mechanism",
   "steps": [{"kind": "<one of the intervention kinds>",
              "params": {"<field>": <number>},
              "rationale": "why this step, this order"}]}
]"""
        try:
            raw = client.ask_json(self.pi_model, prompt, PI_SYSTEM, schema,
                                  max_tokens=2500)
        except client.FleetError as exc:
            notes.append(f"PI call failed ({exc}); using the built-in panel")
            return self._use_fallback(count), notes

        candidates, names = [], []
        for entry in raw if isinstance(raw, list) else [raw]:
            try:
                steps = [Intervention.from_dict(s) for s in entry.get("steps", [])]
            except (InterventionError, AttributeError) as exc:
                notes.append(f"rejected {entry.get('name', 'unnamed')!r}: {exc}")
                continue
            if steps:
                candidates.append(steps)
                names.append(entry.get("name", "unnamed"))

        if not candidates:
            notes.append("no valid protocol survived validation; using the built-in panel")
            return self._use_fallback(count), notes

        self._names = names
        return candidates, notes

    # -- 2. simulate -------------------------------------------------------

    def simulate(self, candidates: list[list[Intervention]]) -> list[Outcome]:
        names = getattr(self, "_names", None)
        outcomes = []
        for index, steps in enumerate(candidates):
            label = names[index] if names and index < len(names) else f"protocol {index + 1}"
            outcomes.append(run_protocol(self.patient, steps, label, self.params))
        return outcomes

    # -- 3. review ---------------------------------------------------------

    def review(self, outcomes: list[Outcome], on_review=None) -> list[Review]:
        """Three specialists, each reading the same engine output from one angle.

        Run concurrently. They share no state and never see each other's answers,
        so sequential execution buys nothing and costs the sum of three model
        latencies — measured at 6.5 minutes on a slow provider, which reads as a
        hang. `on_review` fires as each one lands, so the UI fills in gradually
        instead of staying empty until the slowest returns.
        """
        if self.offline or not outcomes:
            return []

        results = "\n\n".join(o.as_table() for o in outcomes)
        allowed = guard.allowed_from(outcomes, results, self.patient.describe())

        angles = {
            "immunologist": "Does the mechanism hold together? Which protocol changes "
                            "the immunology most durably, and which only suppresses a "
                            "readout without changing the underlying sensitisation?",
            "pharmacologist": "Is the dosing coherent? Comment on whether the starting "
                              "dose sits above the patient's own threshold, and on what "
                              "the schedule would have to look like in practice.",
            "toxicologist": "Where is the risk? Identify which protocol carries the "
                            "greatest chance of a reaction during treatment, and what "
                            "the simulation does not model that would matter.",
        }

        def one(role: str, question: str) -> Review:
            prompt = f"""<results>
{results}
</results>

<your_angle>
{question}
</your_angle>

<task>
Answer in at most 120 words. Cite only numbers that appear in the results above.
</task>"""
            try:
                text = client.call(self.review_model, prompt, REVIEW_SYSTEM,
                                   max_tokens=700)
            except client.FleetError as exc:
                return Review(role, self.review_model, f"[unavailable: {exc}]")
            return Review(role, self.review_model, text.strip(),
                          guard.audit_text(text, allowed))

        reviews: list[Review] = []
        with ThreadPoolExecutor(max_workers=len(angles)) as pool:
            futures = {pool.submit(one, role, question): role
                       for role, question in angles.items()}
            for future in as_completed(futures):
                review = future.result()
                reviews.append(review)
                if on_review:
                    on_review(review)
        # Restore the declared order, so a report does not shuffle by latency.
        order = list(angles)
        reviews.sort(key=lambda r: order.index(r.role))
        return reviews

    # -- 4. judge ----------------------------------------------------------

    def judge(self, outcomes: list[Outcome], reviews: list[Review], goal: str
              ) -> tuple[str, list[guard.Claim]]:
        if self.offline or not outcomes:
            return "", []

        results = "\n\n".join(o.as_table() for o in outcomes)
        panel = "\n\n".join(f"[{r.role}]\n{r.text}" for r in reviews) or "(no reviews)"
        allowed = guard.allowed_from(outcomes, results, panel, self.patient.describe())

        prompt = f"""<goal>
{goal}
</goal>

<simulation_results>
{results}
</simulation_results>

<specialist_reviews>
{panel}
</specialist_reviews>

<task>
Rank the protocols and recommend one. Then state plainly what this simulation
does not establish. Be specific about the second part — the eliciting dose is a
simulated threshold, not a measured one, and the model contains parameters that
were fitted rather than observed. At most 200 words.
</task>"""
        try:
            text = client.call(self.judge_model, prompt, JUDGE_SYSTEM, max_tokens=1200)
        except client.FleetError as exc:
            return f"[judge unavailable: {exc}]", []
        return text.strip(), guard.audit_text(text, allowed)

    # -- the whole loop ----------------------------------------------------

    def run(self, goal: str, count: int = 4, progress=None) -> Session:
        """Design, simulate, review, judge.

        `progress(stage, payload)` is called as each stage completes, so a caller
        can stream the session while it runs. The whole loop takes 30-60 seconds
        against live models, which is far too long to show nothing.
        """
        emit = progress or (lambda *_: None)

        emit("design", {"model": self.pi_model, "offline": self.offline})
        candidates, notes = self.design(goal, count)
        emit("designed", {
            "notes": notes,
            "protocols": [
                {"label": getattr(self, "_names", [])[i] if i < len(getattr(self, "_names", [])) else f"protocol {i+1}",
                 "steps": [s.describe() for s in steps]}
                for i, steps in enumerate(candidates)],
        })

        emit("simulate", {"count": len(candidates)})
        outcomes = self.simulate(candidates)
        emit("simulated", {"outcomes": [_outcome_json(o) for o in outcomes]})

        emit("review", {"model": self.review_model, "roles": 3})
        reviews = self.review(outcomes, on_review=lambda r: emit("reviewed", {
            "role": r.role, "model": r.model, "text": r.text,
            "untraceable": [c.value for c in r.untraceable]}))

        emit("judge", {"model": self.judge_model})
        verdict, untraceable = self.judge(outcomes, reviews, goal)
        emit("judged", {"text": verdict,
                        "untraceable": [c.value for c in untraceable]})

        session = Session(patient=self.patient, goal=goal, candidates=candidates,
                          outcomes=outcomes, reviews=reviews, verdict=verdict,
                          verdict_untraceable=untraceable, notes=notes)

        # The agents chose which mechanisms to combine; the numbers are a search
        # problem, and the engine does that better than any model can. This is
        # also where the objection every review raises — a maintenance dose above
        # the patient's own threshold — stops being advice and becomes a
        # constraint the search has to satisfy.
        best = session.best()
        if best is not None and best.interventions:
            emit("optimise", {"protocol": best.label,
                              "steps": [s.describe() for s in best.interventions]})
            try:
                tuned = optimise(self.patient, best.interventions, self.params)
                session.optimisation = tuned
                emit("optimised", {
                    "protocol": best.label,
                    "evaluations": tuned.evaluations,
                    "rejected_unsafe": tuned.rejected_unsafe,
                    "improved": tuned.improved,
                    "gain": tuned.gain if math.isfinite(tuned.gain) else "Infinity",
                    "before": _outcome_json(tuned.baseline.outcome),
                    "after": _outcome_json(tuned.best.outcome),
                    "steps": [{"kind": s.kind, "params": s.params}
                              for s in tuned.best.steps],
                    "safe_start": tuned.best.safe_start,
                    "notes": tuned.notes,
                })
            except Exception as exc:
                emit("optimised", {"error": f"{type(exc).__name__}: {exc}"})

        emit("done", {"best": best.label if best else None,
                      "usage": client.USAGE.summary()})
        return session


def _outcome_json(outcome: Outcome) -> dict:
    """An Outcome as plain JSON, with infinities the browser can read."""
    import math as _math

    def clean(value):
        if isinstance(value, float) and not _math.isfinite(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value

    return {
        "label": outcome.label,
        "steps": [step.describe() for step in outcome.interventions],
        "eliciting_dose_before_mg": clean(outcome.eliciting_dose_before_mg),
        "eliciting_dose_after_mg": clean(outcome.eliciting_dose_after_mg),
        "fold_shift": clean(outcome.fold_shift),
        "glass_score_before": outcome.glass_score_before,
        "glass_score_after": outcome.glass_score_after,
        "specific_ige_ku": outcome.specific_ige_ku,
        "igg4_mg_l": outcome.igg4_mg_l,
        "treg": outcome.treg,
        "mucosal_barrier": outcome.mucosal_barrier,
        "protects_against_a_glass": outcome.protects_against_a_glass,
    }


def render(session: Session) -> str:
    """A session as a readable report."""
    bar = "=" * 78
    lines = [bar, f"TIKKUN LAB — {session.goal}", bar,
             f"  {session.patient.describe()}", ""]

    for note in session.notes:
        lines.append(f"  note: {note}")
    if session.notes:
        lines.append("")

    lines += ["SIMULATED PROTOCOLS", "-" * 78]
    ranked = sorted(session.outcomes, key=lambda o: -o.fold_shift)
    lines.append(f"  {'protocol':<28} {'threshold shift':>16} {'IgG4':>10}  protects a glass")
    lines.append(f"  {'-' * 28} {'-' * 16} {'-' * 10}  {'-' * 16}")
    for outcome in ranked:
        shift = f"{outcome.fold_shift:.1f}x" if outcome.fold_shift < 1e6 else "complete"
        lines.append(f"  {outcome.label[:28]:<28} {shift:>16} "
                     f"{outcome.igg4_mg_l:>7.1f} mg/L  "
                     f"{'yes' if outcome.protects_against_a_glass else 'no':>16}")

    best = session.best()
    if best:
        lines += ["", "  best by engine measurement:", ""]
        lines += [f"    {line}" for line in best.as_table().splitlines()]

    if session.reviews:
        lines += ["", "SPECIALIST REVIEW", "-" * 78]
        for review in session.reviews:
            lines += [f"  [{review.role}] via {review.model}", f"    {review.text}",
                      guard.report(review.untraceable), ""]

    if session.verdict:
        lines += ["CRITIC", "-" * 78, f"  {session.verdict}",
                  guard.report(session.verdict_untraceable), ""]

    lines += [bar, f"  {client.USAGE.summary()}", bar]
    return "\n".join(lines)
