"""Run the JavaScript-side checks from pytest, so one command covers everything.

There are three of them and they check different things:

  verify.mjs          the browser engine agrees with the Python engine, value by
                      value, against a fixture generated from Python
  frontend-check.mjs  app.js actually boots — module graph, controls, first
                      recompute, both charts — against a DOM shim
  smoke.mjs           the built standalone page is self-contained and computes

Skipped when Node is not installed, rather than failing: the Python engine, the
agent layer and the server all work without it.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, os.path.join(WEB, script)],
                          capture_output=True, text=True, timeout=300, cwd=WEB)


@pytest.mark.parametrize("script, evidence", [
    ("verify.mjs", "values agree"),
    ("frontend-check.mjs", "boots clean"),
])
def test_node_check_passes(script, evidence):
    result = run_node(script)
    assert result.returncode == 0, (
        f"{script} failed:\n{result.stdout}\n{result.stderr}")
    assert evidence in result.stdout, f"{script} did not report success:\n{result.stdout}"


def test_built_page_smoke_test_passes():
    if not os.path.isfile(os.path.join(WEB, "lab.html")):
        pytest.skip("web/lab.html not built — run `node web/build.mjs`")
    result = run_node("smoke.mjs")
    assert result.returncode == 0, f"smoke.mjs failed:\n{result.stdout}\n{result.stderr}"
