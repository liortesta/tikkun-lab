"""Cow's-milk allergy: the deterministic core of the simulator.

Two timescales, deliberately kept separate because the biology separates them:

  * `challenge()` — minutes. An oral dose reaches mucosal mast cells, crosslinks
    IgE on FcepsilonRI, degranulation follows, histamine rises and clears.
  * `immunotherapy()` — months. Daily dosing drives blocking IgG4 up, induces
    regulatory T cells, and those in turn suppress IgE production.

The link between them is the *eliciting dose*: run a course of immunotherapy,
then re-measure the dose that triggers objective symptoms. That shift is the
number real milk-OIT trials report, and it is what this model exists to predict.

Symptoms arrive on two channels, because food-allergic reactions do:

  * local — mucosal degranulation drives vomiting and abdominal pain through
    enteric reflexes, with barely any systemic histamine. Set by how hard each
    engaged mast cell is crosslinked.
  * systemic — plasma histamine drives flushing, tachycardia and hypotension.
    Set by how much mast cell mass degranulates, so it needs a real dose.

That split is why a trace exposure makes a sensitive child vomit while a glass
of milk causes anaphylaxis: different channels, not different severities of one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.integrate import solve_ivp

from .binding import crosslinks, free_allergen, hill, sensitized_receptors
from .params_milk import MILK
from .provenance import Registry

LN2 = math.log(2.0)

#: Integrator settings. Fixed so two runs of the same input give the same output
#: to the last digit — a simulator whose results move between runs cannot be
#: used to compare interventions.
_IVP = dict(method="LSODA", rtol=1e-8, atol=1e-11, dense_output=True)


@dataclass(frozen=True)
class Patient:
    """The immunological state that decides how someone reacts.

    `specific_ige_ku` and `total_ige_ku` are the two numbers an allergist
    actually has on a lab report, which is the point — the model takes clinical
    inputs, not model-internal abstractions.

    `mucosal_barrier` is everything else that shifts a threshold without
    touching IgE: baseline gut permeability, and the co-factors documented to
    lower reaction thresholds several-fold — exercise, fever, NSAIDs, alcohol
    (Niggemann & Beyer 2014, Allergy 69:1582). 1.0 is an intact barrier at rest.
    """

    label: str = "default"
    specific_ige_ku: float = 15.0
    total_ige_ku: float = 400.0
    igg4_m: float = 2.0e-9
    treg: float = 0.0
    mucosal_barrier: float = 1.0

    def describe(self) -> str:
        return (
            f"{self.label}: milk-sIgE {self.specific_ige_ku:.1f} kU/L, "
            f"total IgE {self.total_ige_ku:.0f} kU/L, "
            f"sIgG4 {self.igg4_m * 1e9:.1f} nM, Treg {self.treg:.2f}, "
            f"barrier {self.mucosal_barrier:.1f}x"
        )


#: Presets spanning the clinical range. Specific IgE values are real decision
#: points: 15 kU/L is the 95% positive predictive value for milk allergy
#: (Sampson 2001, J Allergy Clin Immunol 107:891), and below ~2 kU/L most
#: children have outgrown it.
PATIENTS = {
    "exquisite": Patient("exquisite", specific_ige_ku=100.0, total_ige_ku=300.0,
                         mucosal_barrier=2.5),
    "default": Patient("default", specific_ige_ku=15.0, total_ige_ku=400.0),
    "moderate": Patient("moderate", specific_ige_ku=5.0, total_ige_ku=400.0),
    "outgrowing": Patient("outgrowing", specific_ige_ku=1.2, total_ige_ku=350.0),
}


@dataclass
class ChallengeResult:
    dose_mg: float
    free_allergen_m: float
    crosslinks_per_cell: float
    activation: float
    engaged_fraction: float
    peak_histamine: float
    time_to_peak_s: float
    local_score: float
    systemic_score: float
    symptom_score: float
    reaction: bool
    trace_t: np.ndarray = field(repr=False, default=None)
    trace_histamine: np.ndarray = field(repr=False, default=None)


@dataclass
class CourseResult:
    days: np.ndarray = field(repr=False, default=None)
    specific_ige_ku: np.ndarray = field(repr=False, default=None)
    igg4_m: np.ndarray = field(repr=False, default=None)
    treg: np.ndarray = field(repr=False, default=None)
    final: Patient = None
    daily_dose_mg: float = 0.0


# ----------------------------------------------------------------------------
# Fast timescale: a single oral food challenge
# ----------------------------------------------------------------------------

def mucosal_exposure(dose_mg: float, p: Registry, barrier: float = 1.0) -> float:
    """Beta-lactoglobulin reaching the mucosal mast cell, mol/L.

    Saturable, because epithelial transport saturates. But `mucosal_km` sits far
    above the clinical dose range, so in practice this is near-linear in dose —
    which matters, because a transport step that saturated inside the clinical
    range would make less-sensitive patients unable to react to any dose at all.

    `barrier` scales delivery for one patient: a leaky gut or an active co-factor
    puts more allergen through the same dose.
    """
    if dose_mg <= 0.0:
        return 0.0
    return barrier * p.mucosal_cmax * dose_mg / (dose_mg + p.mucosal_km)


def engaged_mast_cells(dose_mg: float, p: Registry) -> float:
    """Fraction of the mucosal mast cell pool the dose actually reaches."""
    if dose_mg <= 0.0:
        return 0.0
    return dose_mg / (dose_mg + p.recruit_km)


def challenge(
    patient: Patient,
    dose_mg: float,
    p: Registry = MILK,
    duration_s: float = 7200.0,
    keep_trace: bool = False,
) -> ChallengeResult:
    """One oral dose of milk protein, in mg, given to one patient.

    Returns the full picture: how much allergen reached the mast cells, how many
    receptor crosslinks that produced, how much histamine followed, and where
    that lands on the 0-10 PRACTALL severity scale.
    """
    plateau = mucosal_exposure(dose_mg, p, patient.mucosal_barrier)
    free_m = free_allergen(plateau, patient.igg4_m, p.k_igg4)

    receptors = sensitized_receptors(
        patient.specific_ige_ku, patient.total_ige_ku,
        p.receptors_per_cell, p.k_occupancy_ku,
    )
    x = crosslinks(free_m, receptors, p.k_bind, p.k_cross)
    activation = hill(x, p.crosslink_threshold, p.crosslink_hill)
    engaged = engaged_mast_cells(dose_mg, p)

    k_deg = p.k_degranulate
    k_clear = LN2 / p.histamine_halflife
    k_absorb = 1.0 / p.absorption_tmax
    baseline = p.histamine_baseline
    yield_ng = p.histamine_yield
    igg4, k_igg4 = patient.igg4_m, p.k_igg4
    k_bind, k_cross = p.k_bind, p.k_cross
    threshold, coop = p.crosslink_threshold, p.crosslink_hill

    def rhs(t, y):
        degranulated, histamine = y
        # Allergen has to cross the gut before it can do anything. Two sequential
        # transit steps — stomach, then intestine — give an Erlang-2 arrival
        # profile whose slope starts at zero, so nothing reaches the mast cells
        # in the first moments. A single exponential has its steepest rise at
        # t=0 and fires the model within seconds of swallowing, which is wrong:
        # real food reactions are gated by gastric emptying.
        x = k_absorb * t
        arrived = plateau * (1.0 - (1.0 + x) * math.exp(-x))
        bridgeable = free_allergen(arrived, igg4, k_igg4)
        active = hill(crosslinks(bridgeable, receptors, k_bind, k_cross), threshold, coop)
        flux = k_deg * active * max(0.0, engaged - degranulated)
        return [flux, yield_ng * flux - k_clear * (histamine - baseline)]

    sol = solve_ivp(rhs, (0.0, duration_s), [0.0, baseline],
                    t_eval=np.linspace(0.0, duration_s, 481), **_IVP)

    histamine = sol.y[1]
    peak_idx = int(np.argmax(histamine))
    peak = float(histamine[peak_idx])

    local = p.symptom_max * activation * p.local_weight
    systemic = p.symptom_max * hill(peak, p.symptom_ec50, p.symptom_hill)
    # Probabilistic OR: either channel alone can produce symptoms, and the two
    # together saturate rather than summing past the top of the scale.
    combined = p.symptom_max * (
        1.0 - (1.0 - local / p.symptom_max) * (1.0 - systemic / p.symptom_max)
    )

    return ChallengeResult(
        dose_mg=dose_mg,
        free_allergen_m=free_m,
        crosslinks_per_cell=x,
        activation=activation,
        engaged_fraction=engaged,
        peak_histamine=peak,
        time_to_peak_s=float(sol.t[peak_idx]),
        local_score=local,
        systemic_score=systemic,
        symptom_score=combined,
        reaction=combined >= p.reaction_threshold,
        trace_t=sol.t if keep_trace else None,
        trace_histamine=histamine if keep_trace else None,
    )


def eliciting_dose(
    patient: Patient,
    p: Registry = MILK,
    lo_mg: float = 1e-5,
    hi_mg: float = 1e5,
    tol: float = 1e-3,
) -> float:
    """Smallest dose of milk protein producing objective symptoms, in mg.

    This is the clinical endpoint: the eliciting dose reported by food challenge
    studies and the quantity oral immunotherapy is trying to move. Bisected in
    log space because it spans six orders of magnitude across patients.
    """
    if challenge(patient, lo_mg, p).reaction:
        return lo_mg
    if not challenge(patient, hi_mg, p).reaction:
        return math.inf

    log_lo, log_hi = math.log10(lo_mg), math.log10(hi_mg)
    while log_hi - log_lo > tol:
        mid = 0.5 * (log_lo + log_hi)
        if challenge(patient, 10.0**mid, p).reaction:
            log_hi = mid
        else:
            log_lo = mid
    return 10.0**log_hi


def dose_response(
    patient: Patient,
    p: Registry = MILK,
    lo_mg: float = 1e-3,
    hi_mg: float = 1e4,
    points: int = 60,
) -> tuple[np.ndarray, np.ndarray]:
    """Severity score across a log-spaced dose range."""
    doses = np.logspace(math.log10(lo_mg), math.log10(hi_mg), points)
    scores = np.array([challenge(patient, float(d), p).symptom_score for d in doses])
    return doses, scores


# ----------------------------------------------------------------------------
# Slow timescale: a course of oral immunotherapy
# ----------------------------------------------------------------------------

def immunotherapy(
    patient: Patient,
    daily_dose_mg: float,
    days: float = 365.0,
    p: Registry = MILK,
    samples: int = 200,
) -> CourseResult:
    """Daily milk dosing over months, tracking IgG4, Treg and specific IgE.

    Three coupled effects, each on its own clock:

      * IgG4 climbs under sustained antigen exposure (half-life 21 days) and
        intercepts allergen before it reaches the mast cell.
      * Allergen-specific Treg expand over months.
      * Treg suppress IgE production, so specific IgE falls — but slowly, and
        only late, which is exactly the order seen in OIT trials.

    Stopping the daily dose lets Treg decay on their own half-life, which is the
    model's account of why desensitisation is not the same as lasting tolerance.
    """
    igg4_decay = LN2 / p.igg4_halflife
    treg_decay = LN2 / p.treg_halflife
    ige_decay = LN2 / p.sige_halflife

    # Both antibodies need a resting production term, or an untreated patient
    # decays toward zero antibody instead of holding at their own baseline.
    # Ordinary dietary exposure keeps specific IgG4 at its pre-treatment titre,
    # and plasma cells keep specific IgE at its measured level.
    ige_prod = ige_decay * patient.specific_ige_ku
    igg4_prod_baseline = igg4_decay * p.igg4_baseline

    dose_signal_igg4 = hill(daily_dose_mg, p.igg4_dose_km, 1.0)
    dose_signal_treg = hill(daily_dose_mg, p.treg_dose_km, 1.0)

    def rhs(_t, y):
        sige, igg4, treg = y
        d_igg4 = igg4_prod_baseline + p.igg4_kprod * dose_signal_igg4 - igg4_decay * igg4
        d_treg = p.treg_kind * dose_signal_treg * (1.0 - treg) - treg_decay * treg
        d_sige = ige_prod * (1.0 - p.treg_suppression * treg) - ige_decay * sige
        return [d_sige, d_igg4, d_treg]

    y0 = [patient.specific_ige_ku, patient.igg4_m, patient.treg]
    sol = solve_ivp(rhs, (0.0, days), y0,
                    t_eval=np.linspace(0.0, days, samples), **_IVP)

    final = replace(
        patient,
        label=f"{patient.label}+OIT{daily_dose_mg:g}mg/{days:g}d",
        specific_ige_ku=float(sol.y[0, -1]),
        igg4_m=float(sol.y[1, -1]),
        treg=float(sol.y[2, -1]),
    )
    return CourseResult(
        days=sol.t,
        specific_ige_ku=sol.y[0],
        igg4_m=sol.y[1],
        treg=sol.y[2],
        final=final,
        daily_dose_mg=daily_dose_mg,
    )
