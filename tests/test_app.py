"""Tests for the application server.

Runs a real server on an ephemeral port and talks to it over HTTP, because the
things worth testing here are the boundary behaviours — malformed JSON, out-of-
range interventions, path traversal — and those only exist at the boundary.

No model is ever called: the agent stream is exercised in offline mode.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import app as application
from engine import MILK


@pytest.fixture(scope="module")
def server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    httpd = ThreadingHTTPServer(("127.0.0.1", port), application.Handler)
    httpd.verbose = False
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return response.status, json.loads(response.read().decode())


def post(base: str, path: str, body):
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class TestStatic:
    def test_root_serves_the_app(self, server):
        with urllib.request.urlopen(server + "/", timeout=30) as response:
            body = response.read().decode()
        assert response.status == 200
        assert 'src="./app.js"' in body

    def test_serves_the_engine_module(self, server):
        with urllib.request.urlopen(server + "/engine.js", timeout=30) as response:
            body = response.read().decode()
        assert "export function challenge" in body

    def test_unknown_file_is_404(self, server):
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(server + "/nope.js", timeout=30)
        assert caught.value.code == 404

    def test_path_traversal_cannot_escape_the_web_directory(self, server):
        """The server binds to localhost, but a traversal would still hand out
        arbitrary files — including .env."""
        for attempt in ("/../app.py", "/..%2fapp.py", "/../../.env",
                        "/subdir/../../app.py"):
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(server + attempt, timeout=30)
            assert caught.value.code in (403, 404), attempt


class TestReadEndpoints:
    def test_health(self, server):
        status, body = get(server, "/api/health")
        assert status == 200 and body["ok"] is True
        assert body["parameters"] == len(list(MILK))
        assert set(body["providers"]) == {"kie", "openrouter"}

    def test_params_carry_citations(self, server):
        _, body = get(server, "/api/params")
        assert {p["name"] for p in body} == set(MILK)
        assert all(p["source"] for p in body)

    def test_patients_include_thresholds(self, server):
        _, body = get(server, "/api/patients")
        assert body["exquisite"]["eliciting_dose_mg"] < body["default"]["eliciting_dose_mg"]
        # A patient who has outgrown the allergy reacts to nothing, and JSON has
        # no infinity — the browser has to get something it can read back.
        assert body["outgrowing"]["eliciting_dose_mg"] == "Infinity"


class TestChallengeAndProtocol:
    def test_challenge_reproduces_the_calibration_anchor(self, server):
        status, body = post(server, "/api/challenge",
                            {"patient": {"preset": "default"}, "dose_mg": 25.0})
        assert status == 200
        assert body["symptom_score"] == pytest.approx(MILK.reaction_threshold, abs=1e-3)

    def test_challenge_can_return_a_trace(self, server):
        _, body = post(server, "/api/challenge",
                       {"patient": {"preset": "default"}, "dose_mg": 500, "trace": True})
        assert len(body["trace"]) > 100
        assert body["trace"][0][0] == 0.0

    def test_protocol_runs_a_sequence(self, server):
        status, body = post(server, "/api/protocol", {
            "patient": {"preset": "default"},
            "steps": [{"kind": "anti_ige", "params": {"free_ige_reduction": 0.95}},
                      {"kind": "oral_immunotherapy",
                       "params": {"daily_dose_mg": 300, "days": 365}}],
            "label": "anti-IgE then OIT"})
        assert status == 200
        assert body["protects_against_a_glass"] is True

    def test_protocol_panel_returns_every_row(self, server):
        _, body = post(server, "/api/protocols", {"patient": {"preset": "default"}})
        assert len(body) == 5
        assert all("fold_shift" in row for row in body)


class TestValidation:
    def test_malformed_json_is_rejected(self, server):
        status, body = post(server, "/api/challenge", b"{not json")
        assert status == 400 and "invalid JSON" in body["error"]

    def test_non_numeric_patient_field_is_rejected(self, server):
        status, body = post(server, "/api/threshold",
                            {"patient": {"specific_ige_ku": "lots"}})
        assert status == 400 and "must be a number" in body["error"]

    def test_negative_patient_field_is_rejected(self, server):
        status, body = post(server, "/api/threshold",
                            {"patient": {"total_ige_ku": -1}})
        assert status == 400

    def test_unknown_intervention_is_rejected(self, server):
        status, body = post(server, "/api/protocol", {
            "patient": {}, "steps": [{"kind": "gene_therapy", "params": {}}]})
        assert status == 400 and "unknown intervention" in body["error"]

    def test_out_of_range_intervention_is_rejected(self, server):
        """The range check is what stops an agent proposing something the
        published mechanism does not support."""
        status, body = post(server, "/api/protocol", {
            "patient": {}, "steps": [{"kind": "anti_ige",
                                      "params": {"free_ige_reduction": 5}}]})
        assert status == 400 and "outside" in body["error"]

    def test_unknown_endpoint_is_404(self, server):
        status, _ = post(server, "/api/nonsense", {})
        assert status == 404


class TestExperimentLog:
    def test_save_then_read_round_trips(self, server, tmp_path, monkeypatch):
        monkeypatch.setattr(application, "LOG_PATH", str(tmp_path / "experiments.jsonl"))
        status, body = post(server, "/api/log/save", {
            "saved_at": "2026-08-25T10:00:00Z", "note": "בדיקה",
            "patient": {"specific_ige_ku": 15.0},
            "steps": [{"kind": "anti_ige", "params": {"free_ige_reduction": 0.9}}],
            "result": {"fold_shift": 12.0}})
        assert status == 200 and body["saved"] is True

        _, entries = get(server, "/api/log")
        assert entries[-1]["note"] == "בדיקה"
        assert entries[-1]["result"]["fold_shift"] == 12.0

    def test_clear_empties_the_log(self, server, tmp_path, monkeypatch):
        monkeypatch.setattr(application, "LOG_PATH", str(tmp_path / "experiments.jsonl"))
        post(server, "/api/log/save", {"note": "x", "result": {}})
        post(server, "/api/log/clear", {})
        _, entries = get(server, "/api/log")
        assert entries == []


class TestAgentStream:
    def test_offline_session_streams_every_stage(self, server):
        """Offline, so no model is called and no key is needed."""
        query = ("goal=protect+against+accidental+exposure&count=3&offline=1"
                 "&patient=%7B%22preset%22%3A%22default%22%7D")
        stages, payloads = [], {}
        with urllib.request.urlopen(server + "/api/lab/stream?" + query,
                                    timeout=120) as response:
            stage = None
            for raw in response:
                line = raw.decode("utf-8").rstrip()
                if line.startswith("event:"):
                    stage = line[6:].strip()
                    stages.append(stage)
                elif line.startswith("data:") and stage:
                    payloads[stage] = json.loads(line[5:])

        assert stages[0] == "design"
        assert stages[-1] == "done"
        for required in ("designed", "simulated", "done"):
            assert required in payloads, f"never received {required}"
        assert len(payloads["simulated"]["outcomes"]) == 3
        assert payloads["done"]["best"]

    def test_stream_rejects_a_malformed_patient(self, server):
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                server + "/api/lab/stream?offline=1&patient=notjson", timeout=30)
        assert caught.value.code == 400
