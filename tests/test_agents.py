import io

import pytest
from pypdf import PdfWriter

from app.agents.providers.stub import StubProvider
from app.config import settings
from tests.conftest import INGEST_PAYLOAD, login

BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d">
  <process id="p1" name="Pago con tarjeta">
    <laneSet id="ls">
      <lane id="l1" name="Cliente"><flowNodeRef>t1</flowNodeRef></lane>
      <lane id="l2" name="Pasarela"><flowNodeRef>t2</flowNodeRef></lane>
    </laneSet>
    <startEvent id="s" name="Inicio"/>
    <userTask id="t1" name="Ingresar tarjeta"><documentation>Datos del titular</documentation></userTask>
    <serviceTask id="t2" name="Validar tarjeta"/>
    <exclusiveGateway id="g" name="Aprobada?"/>
    <endEvent id="e" name="Fin"/>
    <sequenceFlow id="f1" sourceRef="s" targetRef="t1"/>
  </process>
</definitions>
"""

XXE_BPMN = """<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="p"><userTask id="t" name="&xxe;"/></process></definitions>
"""


@pytest.fixture
def agents(monkeypatch, tmp_path):
    """Proveedor stub, ejecución inline y carpeta de documentos temporal."""
    monkeypatch.setattr(settings, "agent_provider", "stub")
    monkeypatch.setattr(settings, "agent_execution", "inline")
    monkeypatch.setattr(settings, "agent_docs_dir", str(tmp_path))
    monkeypatch.setattr(settings, "agent_auto_refine", False)
    return tmp_path


@pytest.fixture
def demo(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    return client


def generate(client, **body):
    return client.post("/api/v1/projects/DEMO/agents/generate-stories", json=body)


def test_status_reports_not_configured_by_default(demo, monkeypatch):
    monkeypatch.setattr(settings, "agent_provider", "none")
    login(demo, "po@demo.co")
    status = demo.get("/api/v1/agents/status").json()
    assert status["configured"] is False
    assert "AGENT_PROVIDER" in status["reason"]


def test_generation_without_provider_is_503(demo, monkeypatch):
    monkeypatch.setattr(settings, "agent_provider", "none")
    login(demo, "po@demo.co")
    res = generate(demo, document_text="# Pagos\n- Validar tarjeta del cliente")
    assert res.status_code == 503


def test_generates_borrador_stories_and_is_idempotent(demo, agents):
    login(demo, "po@demo.co")
    text = "# Pagos\n## Actividades\n- [user] Ingresar tarjeta (carril: Cliente): datos del titular\n"
    first = generate(demo, document_text=text, source_name="reunion.md")
    assert first.status_code == 202, first.text
    run = first.json()
    assert run["status"] == "succeeded"
    assert run["result"]["created"] == ["DEMO-3"]  # DEMO-1 y DEMO-2 vienen del ingest

    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-3").json()
    assert story["status"] == "borrador"
    assert story["epic"] == "Pagos"
    assert story["type"] == "HU_TRADICIONAL"
    assert story["acceptance_criteria"].startswith("- [ ] ")
    assert story["source_ref"].startswith("reunion.md")

    second = generate(demo, document_text=text, source_name="reunion.md").json()
    assert second["result"]["created"] == []
    assert len(second["result"]["updated"]) == 1
    assert len(demo.get("/api/v1/projects/DEMO/stories").json()) == 3  # 2 del ingest + 1 generada


def test_folder_bpmn_creates_stories_from_activities(demo, agents):
    project_dir = agents / "DEMO"
    project_dir.mkdir()
    (project_dir / "proceso.bpmn").write_text(BPMN, encoding="utf-8")
    login(demo, "po@demo.co")

    run = generate(demo, filename="proceso.bpmn").json()
    assert run["status"] == "succeeded", run
    titles = {s["title"] for s in demo.get("/api/v1/projects/DEMO/stories").json()}
    assert {"Ingresar tarjeta", "Validar tarjeta"} <= titles
    listed = demo.get("/api/v1/projects/DEMO/agents/documents").json()["documents"]
    assert [d["name"] for d in listed] == ["proceso.bpmn"]


def test_hostile_bpmn_is_rejected(demo, agents):
    (agents / "DEMO").mkdir()
    (agents / "DEMO" / "malo.bpmn").write_text(XXE_BPMN, encoding="utf-8")
    login(demo, "po@demo.co")
    res = generate(demo, filename="malo.bpmn")
    assert res.status_code == 422
    assert "BPMN" in res.json()["detail"]


def test_path_traversal_and_missing_files_are_rejected(demo, agents):
    login(demo, "po@demo.co")
    assert generate(demo, filename="../secreto.md").status_code == 422
    assert generate(demo, filename="no-existe.md").status_code == 404


def test_pdf_without_text_is_rejected(demo, agents):
    (agents / "DEMO").mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    (agents / "DEMO" / "escaneado.pdf").write_bytes(buffer.getvalue())
    login(demo, "po@demo.co")
    res = generate(demo, filename="escaneado.pdf")
    assert res.status_code == 422
    assert "OCR" in res.json()["detail"]


def test_developer_and_stakeholder_cannot_generate(demo, agents):
    for user in ("dev@demo.co", "stake@demo.co"):
        login(demo, user)
        assert generate(demo, document_text="# X\n- algo suficientemente largo").status_code == 403


def test_document_over_limit_is_413(demo, agents, monkeypatch):
    monkeypatch.setattr(settings, "agent_max_doc_chars", 50)
    login(demo, "po@demo.co")
    assert generate(demo, document_text="x" * 120).status_code == 413


def test_provider_failure_is_recorded_and_creates_no_stories(demo, agents, monkeypatch):
    def boom(self, data):
        raise RuntimeError("cuota agotada")

    monkeypatch.setattr(StubProvider, "generate_stories", boom)
    login(demo, "po@demo.co")
    run = generate(demo, document_text="# Pagos\n- Validar tarjeta del cliente").json()
    assert run["status"] == "failed"
    assert "cuota agotada" in run["error"]
    assert len(demo.get("/api/v1/projects/DEMO/stories").json()) == 2  # solo las del ingest


def test_refinement_by_agent_ends_refinada_never_lista(demo, agents):
    login(demo, "po@demo.co")
    run = demo.post("/api/v1/projects/DEMO/stories/DEMO-1/agents/refine", json={}).json()
    assert run["status"] == "succeeded", run
    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-1").json()
    assert story["status"] == "refinada"
    assert story["acceptance_criteria"].startswith("- [ ] ")
    events = demo.get("/api/v1/projects/DEMO/stories/DEMO-1/events").json()
    agent_events = [e for e in events if e["actor_name"] == "agent:stub"]
    assert any(e["event_type"] == "status_changed" and e["to_status"] == "refinada" for e in agent_events)
    assert any(e["event_type"] == "refinement_notes" for e in agent_events)


def test_refinement_failure_marks_request_failed(demo, agents, monkeypatch):
    def boom(self, data):
        raise RuntimeError("timeout del modelo")

    monkeypatch.setattr(StubProvider, "refine_story", boom)
    login(demo, "po@demo.co")
    run = demo.post("/api/v1/projects/DEMO/stories/DEMO-1/agents/refine", json={}).json()
    assert run["status"] == "failed"
    story = demo.get("/api/v1/projects/DEMO/stories/DEMO-1").json()
    assert story["status"] == "en_refinamiento"  # un humano decide: volver a borrador o reintentar


def test_auto_refine_only_when_enabled(demo, agents, monkeypatch):
    login(demo, "po@demo.co")
    demo.post("/api/v1/projects/DEMO/stories/DEMO-1/status", json={"status": "en_refinamiento"})
    assert demo.get("/api/v1/projects/DEMO/agents/runs").json() == []

    demo.post("/api/v1/projects/DEMO/stories/DEMO-1/status", json={"status": "borrador"})
    monkeypatch.setattr(settings, "agent_auto_refine", True)
    demo.post("/api/v1/projects/DEMO/stories/DEMO-1/status", json={"status": "en_refinamiento"})
    runs = demo.get("/api/v1/projects/DEMO/agents/runs").json()
    assert len(runs) == 1 and runs[0]["status"] == "succeeded"
    assert demo.get("/api/v1/projects/DEMO/stories/DEMO-1").json()["status"] == "refinada"


def test_adk_without_api_key_is_not_configured(demo, monkeypatch):
    monkeypatch.setattr(settings, "agent_provider", "adk")
    monkeypatch.setattr(settings, "google_api_key", None)
    login(demo, "po@demo.co")
    status = demo.get("/api/v1/agents/status").json()
    assert status["configured"] is False
    assert "GOOGLE_API_KEY" in status["reason"]


def web_login(client, email):
    res = client.post("/login", data={"email": email, "password": "clave-segura-123"}, follow_redirects=False)
    assert res.status_code == 303


def test_web_agent_pages_and_forms(demo, agents):
    web_login(demo, "po@demo.co")
    assert demo.get("/p/DEMO/agents").status_code == 200
    backlog = demo.get("/p/DEMO/backlog").text
    assert "Generar HU desde documento" in backlog
    assert "Proveedor: <strong>stub</strong>" in backlog

    form = demo.post(
        "/p/DEMO/agents/generate",
        data={"document_text": "# Pagos\n## Actividades\n- [user] Ingresar tarjeta (carril: Cliente): datos", "source_name": "reunion.md"},
        follow_redirects=False,
    )
    assert form.status_code == 303
    created = demo.get("/api/v1/projects/DEMO/stories/DEMO-3").json()
    assert created["title"] == "Ingresar tarjeta"

    refined = demo.post("/p/DEMO/stories/DEMO-1/agents/refine", data={"instructions": ""}, follow_redirects=False)
    assert refined.status_code == 303
    assert demo.get("/api/v1/projects/DEMO/stories/DEMO-1").json()["status"] == "refinada"
    story_page = demo.get("/p/DEMO/stories/DEMO-3").text
    assert "Refinar con agente IA" in story_page or "Editar historia" in story_page


def test_agents_page_renders_runs_and_failures(demo, agents, monkeypatch):
    web_login(demo, "po@demo.co")
    demo.post("/p/DEMO/agents/generate", data={"document_text": "# Pagos\n- Validar tarjeta del cliente", "source_name": "reunion.md"}, follow_redirects=False)

    def boom(self, data):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(StubProvider, "generate_stories", boom)
    demo.post("/p/DEMO/agents/generate", data={"document_text": "# Otra\n- Otro requisito largo", "source_name": "otra.md"}, follow_redirects=False)
    page = demo.get("/p/DEMO/agents")
    assert page.status_code == 200
    assert "reunion.md" in page.text and "fallo simulado" in page.text and "succeeded" in page.text


def test_web_generate_requires_one_source(demo, agents):
    web_login(demo, "po@demo.co")
    res = demo.post("/p/DEMO/agents/generate", data={"filename": "", "document_text": ""}, follow_redirects=False)
    assert res.status_code == 303
    assert len(demo.get("/api/v1/projects/DEMO/stories").json()) == 2
