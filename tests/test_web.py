"""Guard the browser port against silent drift.

`web/verify.mjs` proves the JavaScript engine matches `web/fixture.json`. But the
fixture is a snapshot: change the Python engine without regenerating it and
verify.mjs happily confirms the JavaScript still matches a stale reference, while
the published lab quietly serves numbers that were never calibrated.

These tests close that loop from the Python side — the fixture must still match
the live engine, and the built page must contain the current parameters.
"""

from __future__ import annotations

import json
import math
import os
import re

import pytest

from engine import MILK, challenge, eliciting_dose, immunotherapy

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
FIXTURE = os.path.join(WEB, "fixture.json")
BUILT = os.path.join(WEB, "lab.html")

REGENERATE = "run `python web/fixture.py`, then `node web/build.mjs`"


@pytest.fixture(scope="module")
def fixture() -> dict:
    if not os.path.isfile(FIXTURE):
        pytest.skip(f"{FIXTURE} not generated yet")
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


def _value(raw):
    return math.inf if raw == "Infinity" else raw


class TestFixtureIsCurrent:
    def test_parameters_match_the_live_registry(self, fixture):
        for name, value in fixture["params"].items():
            assert MILK.meta(name).value == value, f"{name} changed — {REGENERATE}"

    def test_registry_has_no_parameter_missing_from_the_fixture(self, fixture):
        missing = set(MILK) - set(fixture["params"])
        assert not missing, f"new parameters {sorted(missing)} — {REGENERATE}"

    def test_challenges_still_reproduce(self, fixture):
        from engine import PATIENTS
        for row in fixture["challenges"]:
            result = challenge(PATIENTS[row["patient"]], row["dose_mg"])
            where = f"{row['patient']} @ {row['dose_mg']}mg"
            assert result.symptom_score == pytest.approx(
                row["symptom_score"], rel=1e-9), f"{where} — {REGENERATE}"
            assert result.peak_histamine == pytest.approx(
                row["peak_histamine"], rel=1e-9), f"{where} — {REGENERATE}"

    def test_eliciting_doses_still_reproduce(self, fixture):
        from engine import PATIENTS
        for name, want in fixture["eliciting_doses"].items():
            got = eliciting_dose(PATIENTS[name])
            expected = _value(want)
            if math.isinf(expected):
                assert math.isinf(got), f"{name} — {REGENERATE}"
            else:
                assert got == pytest.approx(expected, rel=1e-6), f"{name} — {REGENERATE}"

    def test_immunotherapy_still_reproduces(self, fixture):
        from engine import PATIENTS
        for row in fixture["immunotherapy"]:
            course = immunotherapy(PATIENTS["default"], row["daily_dose_mg"], row["days"])
            where = f"OIT {row['daily_dose_mg']}mg/{row['days']}d"
            assert course.final.igg4_m == pytest.approx(
                row["igg4_m"], rel=1e-9), f"{where} — {REGENERATE}"
            assert course.final.treg == pytest.approx(
                row["treg"], rel=1e-9), f"{where} — {REGENERATE}"


@pytest.fixture(scope="module")
def page() -> str:
    if not os.path.isfile(BUILT):
        pytest.skip("web/lab.html not built yet — run `node web/build.mjs`")
    with open(BUILT, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def page_params(page: str) -> dict[str, float]:
    """The PARAMS object the page ships, parsed back into floats.

    Compared numerically rather than as text: Python renders 6.89949e-08 and
    JavaScript source carries 6.89949e-8. Those are the same number, and a string
    comparison would fail on the formatting while passing on a real drift of, say,
    6.89949e-08 to 6.9e-08.
    """
    block = re.search(r"const PARAMS = \{(.*?)\n\};", page, re.DOTALL)
    assert block, "PARAMS object not found in the built page"
    return {name: float(value)
            for name, value in re.findall(r"(\w+):\s*([-+0-9.eE]+),", block.group(1))}


class TestBuiltPage:
    def test_is_marked_standalone(self, page):
        """The shareable page is the app minus the parts that need a server, so
        it has to announce itself — that flag is what removes those tabs."""
        assert "globalThis.__STANDALONE__ = true" in page

    def test_carries_no_document_skeleton(self, page):
        """The artifact host supplies doctype, html, head and body itself."""
        assert not re.search(r"<!doctype|<html|<body", page, re.IGNORECASE)

    def test_has_no_unresolved_module_imports(self, page):
        """Everything is concatenated into one scope; a surviving import would
        be a request the host blocks."""
        assert not re.search(r"^\s*import\s+[{*\w]", page, re.MULTILINE)

    def test_carries_every_engine_parameter(self, page_params):
        missing = set(MILK) - set(page_params)
        assert not missing, f"page is missing {sorted(missing)} — run `node web/build.mjs`"

    def test_every_shipped_value_equals_the_engine(self, page_params):
        """The values that were calibrated must be the values the page serves."""
        for name, shipped in page_params.items():
            assert shipped == pytest.approx(MILK.meta(name).value, rel=1e-12), \
                f"{name} in the page differs from the engine — run `node web/build.mjs`"

    def test_ships_every_parameter_with_its_provenance(self, page):
        match = re.search(r"globalThis\.__PROVENANCE__ = (\[.*?\]);", page, re.DOTALL)
        assert match, "provenance table missing from the built page"
        entries = json.loads(match.group(1))
        assert {e["name"] for e in entries} == set(MILK)
        for entry in entries:
            assert entry["source"], f"{entry['name']} shipped without a citation"

    def test_loads_no_blocked_external_resource(self, page):
        """The artifact host allows Google Fonts and nothing else."""
        hosts = re.findall(r'(?:src|href)="https?://([^/"]+)', page)
        assert all(h.endswith(("fonts.googleapis.com", "fonts.gstatic.com"))
                   for h in hosts), f"blocked hosts: {hosts}"

    def test_contains_no_api_key(self, page):
        assert not re.search(r"[0-9a-f]{32}", page)
        assert not re.search(r"sk-[A-Za-z0-9_\-]{20,}", page)
