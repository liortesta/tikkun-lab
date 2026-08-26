"""Tests for the agent layer.

None of these call a model. What matters here is the boundary: that an agent
cannot propose something the engine will not honour, and that a fabricated number
in agent prose gets caught.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from agents import (
    LEVERS, Intervention, InterventionError, Lab, apply_intervention, audit_text,
    allowed_from, render, run_protocol,
)
from agents.guard import extract_numbers
from engine import MILK, PATIENTS, eliciting_dose


class TestInterventionValidation:
    def test_rejects_an_unknown_kind(self):
        with pytest.raises(InterventionError, match="unknown intervention"):
            Intervention("gene_therapy", {})

    def test_rejects_an_unknown_parameter(self):
        with pytest.raises(InterventionError, match="no parameter"):
            Intervention("anti_ige", {"free_ige_reduction": 0.9, "dose": 300.0})

    def test_rejects_a_missing_parameter(self):
        with pytest.raises(InterventionError, match="missing"):
            Intervention("oral_immunotherapy", {"daily_dose_mg": 300.0})

    def test_rejects_an_out_of_range_value(self):
        with pytest.raises(InterventionError, match="outside"):
            Intervention("anti_ige", {"free_ige_reduction": 1.5})

    def test_rejects_a_non_numeric_value(self):
        with pytest.raises(InterventionError, match="must be a number"):
            Intervention("anti_ige", {"free_ige_reduction": "most of it"})

    def test_rejects_nan(self):
        with pytest.raises(InterventionError):
            Intervention("anti_ige", {"free_ige_reduction": float("nan")})

    def test_rejects_a_boolean_masquerading_as_a_number(self):
        with pytest.raises(InterventionError, match="must be a number"):
            Intervention("anti_ige", {"free_ige_reduction": True})

    def test_from_dict_rejects_a_non_object(self):
        with pytest.raises(InterventionError):
            Intervention.from_dict("oral_immunotherapy")

    def test_from_dict_rejects_a_missing_kind(self):
        with pytest.raises(InterventionError, match="missing a string 'kind'"):
            Intervention.from_dict({"params": {}})

    def test_from_dict_accepts_a_well_formed_proposal(self):
        step = Intervention.from_dict({
            "kind": "anti_ige", "params": {"free_ige_reduction": 0.9},
            "rationale": "lower receptor occupancy before dosing"})
        assert step.kind == "anti_ige"
        assert "occupancy" in step.rationale

    def test_every_lever_documents_its_evidence(self):
        for lever in LEVERS.values():
            assert lever.mechanism and lever.evidence
            assert lever.fields, f"{lever.kind} has no parameters"


class TestInterventionEffects:
    def test_anti_ige_lowers_both_titres(self):
        patient, _ = apply_intervention(
            Intervention("anti_ige", {"free_ige_reduction": 0.9}),
            PATIENTS["default"], MILK)
        assert patient.specific_ige_ku == pytest.approx(1.5)
        assert patient.total_ige_ku == pytest.approx(40.0)

    def test_anti_ige_raises_the_threshold(self):
        patient, _ = apply_intervention(
            Intervention("anti_ige", {"free_ige_reduction": 0.9}),
            PATIENTS["default"], MILK)
        assert eliciting_dose(patient) > eliciting_dose(PATIENTS["default"])

    def test_passive_igg4_adds_to_what_the_patient_makes(self):
        patient, _ = apply_intervention(
            Intervention("passive_igg4", {"titre_mg_l": 146.0}),
            PATIENTS["default"], MILK)
        assert patient.igg4_m > PATIENTS["default"].igg4_m
        assert patient.igg4_m == pytest.approx(PATIENTS["default"].igg4_m + 1e-6, rel=1e-3)

    def test_barrier_repair_reduces_delivery(self):
        patient, _ = apply_intervention(
            Intervention("barrier_repair", {"permeability_factor": 0.25}),
            PATIENTS["default"], MILK)
        assert patient.mucosal_barrier == pytest.approx(0.25)
        assert eliciting_dose(patient) > eliciting_dose(PATIENTS["default"])

    def test_mast_cell_stabiliser_changes_params_not_the_patient(self):
        patient, params = apply_intervention(
            Intervention("mast_cell_stabiliser", {"threshold_factor": 10.0}),
            PATIENTS["default"], MILK)
        assert patient == PATIENTS["default"]
        assert params.crosslink_threshold == pytest.approx(MILK.crosslink_threshold * 10)

    def test_a_stabiliser_does_not_leak_into_the_global_registry(self):
        before = MILK.crosslink_threshold
        apply_intervention(Intervention("mast_cell_stabiliser", {"threshold_factor": 10.0}),
                           PATIENTS["default"], MILK)
        assert MILK.crosslink_threshold == before


class TestProtocols:
    def test_an_empty_protocol_changes_nothing(self):
        outcome = run_protocol(PATIENTS["default"], [], "control")
        assert outcome.fold_shift == pytest.approx(1.0)
        assert not outcome.protects_against_a_glass

    def test_steps_are_applied_in_order(self):
        """Anti-IgE before dosing is a different protocol from dosing then anti-IgE:
        the first changes what the OIT course starts from."""
        anti = Intervention("anti_ige", {"free_ige_reduction": 0.9})
        oit = Intervention("oral_immunotherapy",
                           {"daily_dose_mg": 300.0, "days": 365.0})
        first = run_protocol(PATIENTS["default"], [anti, oit], "anti-first")
        second = run_protocol(PATIENTS["default"], [oit, anti], "oit-first")
        assert first.specific_ige_ku != second.specific_ige_ku

    def test_outcome_numbers_are_all_finite_or_infinite_by_design(self):
        outcome = run_protocol(
            PATIENTS["default"],
            [Intervention("oral_immunotherapy", {"daily_dose_mg": 300.0, "days": 365.0})],
            "oit")
        for value in outcome.numbers():
            assert not math.isnan(value)

    def test_a_strong_protocol_protects_against_a_glass(self):
        outcome = run_protocol(PATIENTS["default"], [
            Intervention("anti_ige", {"free_ige_reduction": 0.95}),
            Intervention("oral_immunotherapy", {"daily_dose_mg": 300.0, "days": 365.0}),
        ], "anti-IgE then OIT")
        assert outcome.protects_against_a_glass


class TestGuard:
    def test_finds_numbers_in_prose(self):
        found = extract_numbers("threshold rose to 2500 mg, a 99.9x shift")
        assert [v for v, _ in found] == [2500.0, 99.9]

    def test_flags_a_fabricated_number(self):
        claims = audit_text("Peak histamine reached 8.4 ng/mL.", allowed=[25.0, 99.9])
        assert [c.value for c in claims] == [8.4]

    def test_accepts_a_number_that_was_provided(self):
        assert audit_text("The threshold moved to 2503 mg.", allowed=[2502.865]) == []

    def test_accepts_a_rounded_restatement(self):
        """A model writing '100-fold' from 99.87 is restating, not inventing."""
        assert audit_text("roughly a 100-fold shift", allowed=[99.87]) == []

    def test_ignores_citation_years(self):
        assert audit_text("as reported in 2008 and 2016", allowed=[]) == []

    def test_ignores_trivial_numbers(self):
        assert audit_text("all 3 protocols, ranked 1 to 3", allowed=[]) == []

    def test_handles_thousands_separators(self):
        assert audit_text("rose to 2,503 mg", allowed=[2502.865]) == []

    def test_allowed_from_reads_an_outcome(self):
        outcome = run_protocol(PATIENTS["default"], [], "control")
        values = allowed_from(outcome)
        assert outcome.eliciting_dose_before_mg in values

    def test_allowed_from_reads_prompt_text(self):
        assert 15.0 in allowed_from("milk-sIgE 15.0 kU/L, total IgE 400 kU/L")

    def test_allowed_from_walks_nested_structures(self):
        assert 7.5 in allowed_from({"a": [{"b": 7.5}]})


class TestOfflineLab:
    def test_offline_session_runs_without_a_key(self):
        session = Lab(offline=True).run("protect against accidental exposure")
        assert len(session.outcomes) == 4
        assert session.best() is not None
        assert not session.reviews  # no models were called

    def test_offline_protocols_are_named_not_numbered(self):
        session = Lab(offline=True).run("goal")
        assert all(not o.label.startswith("protocol ") for o in session.outcomes)

    def test_the_best_protocol_is_chosen_by_engine_numbers(self):
        session = Lab(offline=True).run("goal")
        best = session.best()
        protective = [o for o in session.outcomes if o.protects_against_a_glass]
        if protective:
            assert best.protects_against_a_glass
            assert best.fold_shift == max(o.fold_shift for o in protective)

    def test_render_produces_a_report(self):
        text = render(Lab(offline=True).run("goal"))
        assert "SIMULATED PROTOCOLS" in text
        assert "eliciting dose" in text

    def test_a_more_sensitive_patient_is_harder_to_protect(self):
        default = Lab(PATIENTS["default"], offline=True).run("goal").best()
        exquisite = Lab(PATIENTS["exquisite"], offline=True).run("goal").best()
        assert exquisite.eliciting_dose_after_mg < default.eliciting_dose_after_mg


class TestKeyHygiene:
    def test_no_api_key_is_hardcoded(self):
        """This repository is meant to be published. A literal key in the source
        would be live the moment it is pushed."""
        import pathlib
        source = pathlib.Path(__file__).parent.parent / "agents" / "client.py"
        text = source.read_text(encoding="utf-8")
        import re
        assert not re.search(r'["\'][0-9a-f]{32}["\']', text), "hex key literal in client"
        assert not re.search(r'["\']sk-[A-Za-z0-9_\-]{20,}["\']', text), "sk- key in client"
