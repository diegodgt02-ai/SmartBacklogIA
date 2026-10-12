"""Método de estimación de HU (etapa 3): fórmula pura, validación de API y ejecución del estimador."""

import pytest

from app.agents import runner
from app.agents.ports import EstimationOutput, Usage
from app.agents.providers.stub import StubProvider
from app.config import settings
from app.services import estimation
from app.services.estimation import EstimationError, estimate
from tests.conftest import INGEST_PAYLOAD, login

IA_LOW = {"datos": 1, "modelo": 1, "evaluacion": 1, "riesgo": 1, "integracion": 1}
IA_HIGH = {"datos": 5, "modelo": 5, "evaluacion": 5, "riesgo": 5, "integracion": 5}
TRAD_LOW = {"complejidad": 1, "esfuerzo": 1, "incertidumbre": 1}


# ---------- fórmula pura ----------

def test_ia_minimum_maps_to_one_point_without_spike():
    result = estimate("HU_IA", IA_LOW)
    assert result["method"] == estimation.IA_METHOD
    assert result["points"] == 1
    assert result["range"] == {"min": 1, "max": 2}
    assert result["spike"] == {"required": False, "points": 0}


def test_ia_maximum_is_capped_at_thirteen_and_requires_spike():
    result = estimate("HU_IA", IA_HIGH)
    assert result["points"] == 13
    assert result["range"]["max"] == 13
    assert result["spike"] == {"required": True, "points": estimation.SPIKE_POINTS}


def test_ia_high_data_alone_requires_spike():
    factors = {**IA_LOW, "datos": 4}
    assert estimate("HU_IA", factors)["spike"]["required"] is True


def test_ia_risk_four_bumps_one_level_and_requires_spike():
    base = {"datos": 2, "modelo": 3, "evaluacion": 3, "riesgo": 3, "integracion": 3}
    base_result = estimate("HU_IA", base)
    risky_result = estimate("HU_IA", {**base, "riesgo": 4})
    # Puntaje bruto 2.75 → 3 pts; con riesgo 4 (raw 2.90) sube un nivel → 5 pts.
    assert (base_result["points"], base_result["raw_score"]) == (3, 2.75)
    assert (risky_result["points"], risky_result["raw_score"]) == (5, 2.9)
    assert risky_result["spike"]["required"] is True
    assert base_result["spike"]["required"] is False


def test_traditional_uses_its_own_method_and_no_spike():
    result = estimate("HU_TRADICIONAL", TRAD_LOW)
    assert result["method"] == estimation.TRADITIONAL_METHOD
    assert result["points"] == 1
    assert result["spike"]["required"] is False


def test_same_factor_values_give_different_method_by_type():
    trad = estimate("HU_TRADICIONAL", {"complejidad": 3, "esfuerzo": 3, "incertidumbre": 3})
    ia = estimate("HU_IA", {"datos": 3, "modelo": 3, "evaluacion": 3, "riesgo": 3, "integracion": 3})
    assert trad["method"] != ia["method"]


@pytest.mark.parametrize(
    "story_type, factors, message",
    [
        (None, IA_LOW, "necesita tipo"),
        ("HU_IA", {**IA_LOW, "datos": 6}, "entre 1 y 5"),
        ("HU_IA", {**IA_LOW, "datos": 0}, "entre 1 y 5"),
        ("HU_IA", {**IA_LOW, "datos": 2.5}, "entero"),
        ("HU_IA", {**IA_LOW, "datos": True}, "entero"),
        ("HU_IA", {"datos": 1}, "faltan"),
        ("HU_TRADICIONAL", {**TRAD_LOW, "datos": 1}, "sobran"),
    ],
)
def test_invalid_input_raises_estimation_error(story_type, factors, message):
    with pytest.raises(EstimationError, match=message):
        estimate(story_type, factors)


# ---------- API ----------

@pytest.fixture
def demo(client, service_key, monkeypatch):
    monkeypatch.setattr(settings, "agent_provider", "stub")
    monkeypatch.setattr(settings, "agent_execution", "inline")
    monkeypatch.setattr(settings, "agent_auto_refine", False)
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    return client


def test_patch_with_factors_computes_points_and_method_on_server(demo, service_key):
    res = demo.patch(
        "/api/v1/projects/DEMO/stories/DEMO-2",
        json={"estimate_factors": IA_HIGH},
        headers={"X-API-Key": service_key},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["estimate_points"] == 13
    assert body["estimate_method"] == estimation.IA_METHOD
    assert body["estimate_factors"]["factors"] == IA_HIGH
    assert body["estimate_factors"]["version"] == 1


def test_patch_rejects_method_that_does_not_match_type(demo, service_key):
    res = demo.patch(
        "/api/v1/projects/DEMO/stories/DEMO-2",
        json={"estimate_factors": IA_LOW, "estimate_method": estimation.TRADITIONAL_METHOD},
        headers={"X-API-Key": service_key},
    )
    assert res.status_code == 422
    assert "no corresponde al tipo" in res.json()["detail"]


def test_patch_rejects_factor_out_of_range(demo, service_key):
    res = demo.patch(
        "/api/v1/projects/DEMO/stories/DEMO-2",
        json={"estimate_factors": {**IA_LOW, "datos": 9}},
        headers={"X-API-Key": service_key},
    )
    assert res.status_code == 422


def test_manual_points_still_accepted_without_factors(demo, service_key):
    res = demo.patch(
        "/api/v1/projects/DEMO/stories/DEMO-1",
        json={"estimate_points": 5},
        headers={"X-API-Key": service_key},
    )
    assert res.status_code == 200
    assert res.json()["estimate_points"] == 5


def test_estimate_endpoint_runs_stub_and_records_history(demo, service_key):
    res = demo.post(
        "/api/v1/projects/DEMO/stories/DEMO-2/agents/estimate",
        json={},
        headers={"X-API-Key": service_key},
    )
    assert res.status_code == 202, res.text
    run = res.json()
    assert run["kind"] == "estimate_story"
    assert run["status"] == "succeeded", run["error"]
    assert run["result"]["method"] == estimation.IA_METHOD
    assert run["result"]["points"] is not None

    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-2", headers={"X-API-Key": service_key}).json()
    assert story["estimate_points"] == run["result"]["points"]
    events = demo.get("/api/v1/projects/DEMO/stories/DEMO-2/events", headers={"X-API-Key": service_key}).json()
    assert any(e["event_type"] == "estimation_rationale" for e in events)


def test_estimate_story_without_type_is_422(demo, service_key):
    demo.post("/api/v1/projects/DEMO/stories", json={"title": "Sin tipo aún"}, headers={"X-API-Key": service_key})
    res = demo.post(
        "/api/v1/projects/DEMO/stories/DEMO-3/agents/estimate",
        json={},
        headers={"X-API-Key": service_key},
    )
    assert res.status_code == 422


def test_estimator_with_incomplete_factors_is_recorded_and_keeps_story_unchanged(demo, service_key, monkeypatch):
    class Incomplete(StubProvider):
        def estimate_story(self, data):
            return EstimationOutput(factors={"datos": 3}), Usage()

    monkeypatch.setattr(runner, "get_provider", lambda: Incomplete())
    res = demo.post(
        "/api/v1/projects/DEMO/stories/DEMO-2/agents/estimate",
        json={},
        headers={"X-API-Key": service_key},
    )
    run = res.json()
    assert run["status"] == "failed"
    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-2", headers={"X-API-Key": service_key}).json()
    assert story["estimate_points"] is None


def test_web_estimate_button_runs_stub_and_shows_points(demo):
    login(demo, "po@demo.co")
    page = demo.get("/p/DEMO/stories/DEMO-2")
    assert "Estimar con agente" in page.text
    res = demo.post("/p/DEMO/stories/DEMO-2/agents/estimate", follow_redirects=False)
    assert res.status_code == 303
    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-2").json()
    assert story["estimate_method"] == estimation.IA_METHOD
    assert story["estimate_points"] is not None


def test_web_edit_form_with_factors_computes_on_server(demo):
    login(demo, "po@demo.co")
    form = {
        "title": "Clasificar fraude con IA", "epic": "", "role_": "", "need": "", "benefit": "", "criteria": "",
        "type": "HU_IA", "type_justification": "", "estimate_points": "", "estimate_method": "",
        "priority_rank": "", "priority_method": "", "source_ref": "",
        "f_datos": "4", "f_modelo": "3", "f_evaluacion": "3", "f_riesgo": "4", "f_integracion": "2",
    }
    res = demo.post("/p/DEMO/stories/DEMO-2/edit", data=form, follow_redirects=False)
    assert res.status_code == 303
    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-2").json()
    assert story["estimate_points"] == 8
    assert story["estimate_factors"]["spike"]["required"] is True
    page = demo.get("/p/DEMO/stories/DEMO-2").text
    assert "Requerido" in page and "Rango" in page


def test_web_partial_factors_are_rejected_with_message(demo):
    login(demo, "po@demo.co")
    form = {"title": "Clasificar fraude con IA", "type": "HU_IA", "f_datos": "4"}
    res = demo.post("/p/DEMO/stories/DEMO-2/edit", data=form, follow_redirects=False)
    assert res.status_code == 303
    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-2").json()
    assert story["estimate_factors"] is None
