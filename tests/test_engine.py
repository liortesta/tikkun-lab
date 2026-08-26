"""Unit tests for the simulation engine.

These cover invariants the equations must satisfy no matter how the parameters
are recalibrated. Whether the model reproduces published clinical numbers is a
separate question, answered by `validate.py`.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from engine import (
    MILK, PATIENTS, Param, Provenance, Registry, challenge, crosslinks,
    dose_response, eliciting_dose, fit_hill, free_allergen, hill, immunotherapy,
    milk_protein_to_blg_molar, sensitized_receptors,
)
from engine.milk import engaged_mast_cells, mucosal_exposure


# ---------------------------------------------------------------------------
# Binding equilibria
# ---------------------------------------------------------------------------

class TestCrosslinking:
    def test_zero_allergen_gives_no_crosslinks(self):
        assert crosslinks(0.0, 1e4, 1e7, 1e-4) == 0.0

    def test_zero_receptors_gives_no_crosslinks(self):
        assert crosslinks(1e-9, 0.0, 1e7, 1e-4) == 0.0

    def test_rises_linearly_at_low_allergen(self):
        """Far below saturation each extra allergen molecule bridges a new pair."""
        low = crosslinks(1e-13, 1e4, 3e7, 2.2e-4)
        ten_x = crosslinks(1e-12, 1e4, 3e7, 2.2e-4)
        assert ten_x / low == pytest.approx(10.0, rel=0.02)

    def test_falls_as_inverse_allergen_far_past_the_peak(self):
        """The prozone limb: every IgE gets its own allergen, none are bridged."""
        high = crosslinks(1e-4, 1e4, 3e7, 2.2e-4)
        ten_x = crosslinks(1e-3, 1e4, 3e7, 2.2e-4)
        assert ten_x / high == pytest.approx(0.1, rel=0.02)

    def test_peak_sits_near_one_over_twice_k_bind(self):
        conc = np.logspace(-12, -4, 500)
        values = [crosslinks(float(c), 1e4, 3e7, 2.2e-4) for c in conc]
        peak = conc[int(np.argmax(values))]
        assert peak == pytest.approx(1.0 / (2 * 3e7), rel=0.25)

    def test_never_exceeds_half_the_receptor_count(self):
        """Each crosslink consumes two receptors, so pairs cannot exceed R/2."""
        for c in np.logspace(-12, -3, 60):
            assert crosslinks(float(c), 1e4, 3e7, 2.2e-4) <= 1e4 / 2 + 1e-9


class TestBlockingAntibody:
    def test_no_igg4_leaves_allergen_untouched(self):
        assert free_allergen(1e-9, 0.0, 1e7) == 1e-9

    def test_blocking_is_quadratic_in_epitope_coverage(self):
        """Crosslinking needs two free epitopes, so coverage bites twice."""
        total, k = 1e-9, 1e7
        igg4 = 1e-7  # K*G = 1, so half the epitopes are covered
        assert free_allergen(total, igg4, k) == pytest.approx(total * 0.25)

    def test_more_igg4_always_blocks_more(self):
        values = [free_allergen(1e-9, g, 1e7) for g in np.logspace(-10, -5, 40)]
        assert all(a >= b for a, b in zip(values, values[1:]))

    def test_saturating_igg4_removes_essentially_all_bridging(self):
        assert free_allergen(1e-9, 1e-3, 1e7) < 1e-9 * 1e-6


class TestSensitizedReceptors:
    def test_no_specific_ige_means_no_sensitized_receptors(self):
        assert sensitized_receptors(0.0, 400.0, 2.4e5, 120.0) == 0.0

    def test_specific_fraction_is_capped_at_one(self):
        """Specific IgE cannot exceed total IgE, even if a lab report says so."""
        both = sensitized_receptors(500.0, 400.0, 2.4e5, 120.0)
        equal = sensitized_receptors(400.0, 400.0, 2.4e5, 120.0)
        assert both == equal

    def test_same_specific_titre_dilutes_against_higher_total(self):
        low_total = sensitized_receptors(50.0, 200.0, 2.4e5, 120.0)
        high_total = sensitized_receptors(50.0, 2000.0, 2.4e5, 120.0)
        assert low_total > high_total


class TestHill:
    def test_zero_input_gives_zero(self):
        assert hill(0.0, 100.0, 2.0) == 0.0

    def test_half_maximal_at_the_half_point(self):
        assert hill(100.0, 100.0, 2.0) == pytest.approx(0.5)

    def test_bounded_in_the_unit_interval(self):
        for x in np.logspace(-6, 9, 60):
            assert 0.0 <= hill(float(x), 100.0, 2.0) <= 1.0


# ---------------------------------------------------------------------------
# Challenge dynamics
# ---------------------------------------------------------------------------

class TestChallenge:
    def test_zero_dose_produces_no_reaction(self):
        result = challenge(PATIENTS["default"], 0.0)
        assert result.crosslinks_per_cell == 0.0
        assert result.peak_histamine == pytest.approx(MILK.histamine_baseline)
        assert not result.reaction

    def test_severity_is_monotone_in_dose(self):
        scores = [challenge(PATIENTS["default"], float(d)).symptom_score
                  for d in np.logspace(-2, 4, 40)]
        assert all(a <= b + 1e-9 for a, b in zip(scores, scores[1:]))

    def test_histamine_never_falls_below_baseline(self):
        result = challenge(PATIENTS["default"], 500.0, keep_trace=True)
        assert result.trace_histamine.min() >= MILK.histamine_baseline - 1e-9

    def test_degranulation_cannot_exceed_the_engaged_pool(self):
        dose = 8000.0
        result = challenge(PATIENTS["default"], dose)
        ceiling = MILK.histamine_yield * engaged_mast_cells(dose, MILK)
        assert result.peak_histamine <= ceiling + MILK.histamine_baseline

    def test_reaction_flag_agrees_with_the_threshold(self):
        for dose in (0.1, 1.0, 25.0, 500.0, 8000.0):
            result = challenge(PATIENTS["default"], dose)
            assert result.reaction == (result.symptom_score >= MILK.reaction_threshold)

    def test_absorption_delays_the_peak_past_one_minute(self):
        """Gastric emptying gates the reaction. Nothing fires in the first seconds."""
        result = challenge(PATIENTS["default"], 8000.0)
        assert result.time_to_peak_s > 60.0

    def test_blocking_antibody_raises_the_threshold(self):
        protected = replace(PATIENTS["default"], igg4_m=4e-7)
        assert eliciting_dose(protected) > eliciting_dose(PATIENTS["default"])

    def test_leaky_barrier_lowers_the_threshold(self):
        leaky = replace(PATIENTS["default"], mucosal_barrier=3.0)
        assert eliciting_dose(leaky) < eliciting_dose(PATIENTS["default"])

    def test_repeated_runs_are_bit_identical(self):
        first = challenge(PATIENTS["default"], 42.0)
        second = challenge(PATIENTS["default"], 42.0)
        assert first.symptom_score == second.symptom_score
        assert first.peak_histamine == second.peak_histamine


class TestElicitingDose:
    def test_returns_infinity_when_no_dose_reacts(self):
        inert = replace(PATIENTS["default"], specific_ige_ku=1e-6)
        assert eliciting_dose(inert) == math.inf

    def test_the_threshold_dose_actually_reacts(self):
        ed = eliciting_dose(PATIENTS["default"])
        assert challenge(PATIENTS["default"], ed).reaction

    def test_just_below_the_threshold_does_not_react(self):
        ed = eliciting_dose(PATIENTS["default"])
        assert not challenge(PATIENTS["default"], ed * 0.98).reaction


class TestMucosalDelivery:
    def test_saturates_at_cmax(self):
        assert mucosal_exposure(1e12, MILK) == pytest.approx(MILK.mucosal_cmax, rel=1e-6)

    def test_clinical_doses_stay_far_below_saturation(self):
        """If delivery saturated inside the clinical range, a larger dose would
        stop helping and insensitive patients could never react at all."""
        assert mucosal_exposure(8000.0, MILK) < 0.75 * MILK.mucosal_cmax

    def test_barrier_scales_delivery_linearly(self):
        base = mucosal_exposure(100.0, MILK, 1.0)
        assert mucosal_exposure(100.0, MILK, 2.5) == pytest.approx(2.5 * base)


# ---------------------------------------------------------------------------
# Immunotherapy
# ---------------------------------------------------------------------------

class TestImmunotherapy:
    def test_zero_dose_leaves_the_patient_unchanged(self):
        course = immunotherapy(PATIENTS["default"], 0.0, 365.0)
        assert course.final.igg4_m == pytest.approx(PATIENTS["default"].igg4_m, rel=0.02)
        assert course.final.treg == pytest.approx(0.0, abs=1e-6)

    def test_specific_ige_is_at_steady_state_without_treatment(self):
        course = immunotherapy(PATIENTS["default"], 0.0, 365.0)
        assert course.final.specific_ige_ku == pytest.approx(
            PATIENTS["default"].specific_ige_ku, rel=1e-4)

    def test_treatment_raises_the_threshold(self):
        course = immunotherapy(PATIENTS["default"], 300.0, 365.0)
        assert eliciting_dose(course.final) > eliciting_dose(PATIENTS["default"])

    def test_longer_treatment_protects_more(self):
        short = immunotherapy(PATIENTS["default"], 300.0, 90.0)
        long = immunotherapy(PATIENTS["default"], 300.0, 365.0)
        assert eliciting_dose(long.final) > eliciting_dose(short.final)

    def test_treg_stays_within_the_unit_interval(self):
        course = immunotherapy(PATIENTS["default"], 3000.0, 730.0)
        assert course.treg.min() >= 0.0
        assert course.treg.max() <= 1.0

    def test_stopping_treatment_loses_protection(self):
        on = immunotherapy(PATIENTS["default"], 300.0, 365.0)
        off = immunotherapy(on.final, 0.0, 365.0)
        assert eliciting_dose(off.final) < eliciting_dose(on.final)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_hill_fit_recovers_a_known_curve(self):
        doses = np.logspace(-1, 3, 40)
        truth = 10.0 / (1.0 + (25.0 / doses) ** 1.5)
        fit = fit_hill(doses, truth)
        assert fit.ec50 == pytest.approx(25.0, rel=0.02)
        assert fit.hill_slope == pytest.approx(1.5, rel=0.02)
        assert fit.r_squared > 0.999

    def test_effective_dose_inverts_the_fitted_curve(self):
        doses, scores = dose_response(PATIENTS["default"], points=50)
        fit = fit_hill(doses, scores)
        ed80 = fit.effective_dose(0.80)
        expected = fit.bottom + 0.80 * (fit.top - fit.bottom)
        assert fit(ed80) == pytest.approx(expected, rel=1e-6)

    def test_effective_dose_rejects_out_of_range_fractions(self):
        fit = fit_hill(*dose_response(PATIENTS["default"], points=40))
        with pytest.raises(ValueError):
            fit.effective_dose(1.0)

    def test_fit_needs_enough_points(self):
        with pytest.raises(ValueError):
            fit_hill([1.0, 2.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_unknown_parameter_raises(self):
        with pytest.raises(AttributeError):
            MILK.not_a_real_parameter

    def test_override_returns_a_copy_and_leaves_the_original_alone(self):
        before = MILK.histamine_yield
        modified = MILK.override(histamine_yield=99.0)
        assert modified.histamine_yield == 99.0
        assert MILK.histamine_yield == before

    def test_override_marks_the_value_as_assumed(self):
        """A hand-set number has no citation behind it any more, and the registry
        must say so rather than inheriting the original's provenance."""
        modified = MILK.override(histamine_yield=99.0)
        assert modified.meta("histamine_yield").provenance is Provenance.ASSUMED

    def test_override_rejects_unknown_parameters(self):
        with pytest.raises(KeyError):
            MILK.override(nonsense=1.0)

    def test_every_parameter_carries_a_source(self):
        for name, param in MILK.items():
            assert param.source, f"{name} has no source"
            assert param.unit, f"{name} has no unit"

    def test_trust_score_reflects_provenance(self):
        strong = Registry({"a": Param(1.0, "-", Provenance.MEASURED, "x")})
        weak = Registry({"a": Param(1.0, "-", Provenance.ASSUMED, "x")})
        assert strong.trust_score() == 1.0
        assert weak.trust_score() == 0.0

    def test_audit_lists_every_parameter(self):
        text = MILK.audit()
        for name in MILK:
            assert name in text


class TestUnitConversion:
    def test_milk_protein_to_molar(self):
        """1 g of milk protein is ~100 mg BLG, which is ~5.46 umol."""
        molar = milk_protein_to_blg_molar(1000.0, volume_l=1.0)
        assert molar == pytest.approx(0.10 * 1.0 / 18300.0, rel=1e-9)
