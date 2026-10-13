"""Prueba el adaptador real de Google ADK (LlmAgent + Runner + sesión + output_schema) sin red.

El modelo es un BaseLlm falso que devuelve JSON canned con uso de tokens. Así se valida
todo el cableado de ADK; lo único que no se prueba aquí es la llamada real a Gemini.
"""

import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agents import runner
from app.agents.ports import GenerationInput, RefinementInput
from app.agents.providers.adk import AdkProvider
from app.config import settings
from tests.conftest import INGEST_PAYLOAD, login

GENERATION_JSON = {
    "stories": [
        {
            "epic": "Pagos",
            "title": "Pagar con tarjeta",
            "role": "cliente",
            "need": "pagar con tarjeta",
            "benefit": "completar la compra",
            "acceptance_criteria": "- [ ] Muestra error si la tarjeta es rechazada",
            "type": "HU_TRADICIONAL",
            "type_justification": "Integración con pasarela",
            "source_section": "Actividad: Validar tarjeta",
        }
    ]
}

REFINEMENT_JSON = {
    "title": "Pagar con tarjeta de crédito",
    "acceptance_criteria": "- [ ] Muestra error claro\n- [ ] Permite reintentar 3 veces",
    "notes": "Se añadieron reintentos.",
}


class FakeGemini(BaseLlm):
    """Imita a Gemini: devuelve siempre el mismo JSON y reporta tokens."""

    model: str = "fake-gemini-pro"
    payload: str = ""
    tokens_in: int = 120
    tokens_out: int = 45

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.payload)]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=self.tokens_in, candidates_token_count=self.tokens_out,
            ),
            turn_complete=True,
        )


def provider_with(payload: dict | str, **tokens) -> AdkProvider:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    fake = FakeGemini(payload=text, **tokens)
    return AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=fake)


def test_generate_stories_through_adk_pipeline():
    provider = provider_with(GENERATION_JSON)
    output, usage = provider.generate_stories(
        GenerationInput(project_key="PAGO", project_name="Pagos", source_name="proceso.bpmn", text="# Pagos")
    )
    assert len(output.stories) == 1
    assert output.stories[0].title == "Pagar con tarjeta"
    assert output.stories[0].acceptance_criteria.startswith("- [ ]")
    assert (usage.tokens_in, usage.tokens_out) == (120, 45)


def test_refine_story_through_adk_pipeline():
    provider = provider_with(REFINEMENT_JSON)
    story = {"code": "PAGO-1", "title": "Pagar", "acceptance_criteria": "- [ ] algo", "version": 2}
    output, _ = provider.refine_story(RefinementInput(story=story))
    assert output.title == "Pagar con tarjeta de crédito"
    assert output.notes == "Se añadieron reintentos."


def test_schema_sent_to_gemini_has_no_length_constraints():
    """Gemini responde 400 si el esquema trae minLength/maxLength/maxItems/pattern."""
    import json as _json

    from app.agents.providers.adk import _LlmGenerationOutput, _LlmRefinementOutput

    for model in (_LlmGenerationOutput, _LlmRefinementOutput):
        text = _json.dumps(model.model_json_schema())
        for forbidden in ("minLength", "maxLength", "minItems", "maxItems", "pattern"):
            assert forbidden not in text, f"{model.__name__} expone {forbidden}"


def test_invalid_model_output_is_a_validation_error():
    from pydantic import ValidationError

    provider = provider_with({"stories": [{"title": "x"}]})  # título demasiado corto
    with pytest.raises(ValidationError):
        provider.generate_stories(GenerationInput(project_key="X", project_name="x", source_name="s", text="t"))


def test_adk_provider_requires_api_key():
    from app.agents.ports import AgentNotConfigured

    with pytest.raises(AgentNotConfigured, match="GOOGLE_API_KEY"):
        AdkProvider(model="gemini-2.5-pro", api_key=None)


@pytest.fixture
def adk_generation(demo_app, monkeypatch, tmp_path):
    client, service_key = demo_app
    monkeypatch.setattr(settings, "agent_provider", "adk")
    monkeypatch.setattr(settings, "agent_execution", "inline")
    monkeypatch.setattr(settings, "agent_price_in_per_1m", 1.25)
    monkeypatch.setattr(settings, "agent_price_out_per_1m", 10.0)
    monkeypatch.setattr(runner, "get_provider", lambda: provider_with(GENERATION_JSON))
    return client


@pytest.fixture
def demo_app(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    return client, service_key


def test_full_generation_run_with_adk_records_tokens_and_cost(adk_generation):
    client = adk_generation
    login(client, "po@demo.co")
    res = client.post("/api/v1/projects/DEMO/agents/generate-stories", json={
        "document_text": "# Pagos\n## Actividades\n- [user] Validar tarjeta (carril: Cliente): datos",
        "source_name": "proceso.bpmn",
    })
    assert res.status_code == 202, res.text
    run = res.json()
    assert run["status"] == "succeeded", run
    assert run["provider"] == "adk" and run["model"] == "gemini-2.5-pro"
    assert run["tokens_in"] == 120 and run["tokens_out"] == 45
    assert run["cost_usd"] == pytest.approx((120 * 1.25 + 45 * 10.0) / 1_000_000, rel=1e-6)
    story = client.get("/api/v1/projects/DEMO/stories/DEMO-3").json()
    assert story["status"] == "borrador"
    assert story["source_ref"] == "proceso.bpmn · Actividad: Validar tarjeta"
    events = client.get("/api/v1/projects/DEMO/stories/DEMO-3/events").json()
    assert events[0]["actor_name"] == "agent:adk"


def test_estimate_story_through_adk_keeps_only_factors_of_the_story_type():
    from app.agents.ports import EstimationInput

    # El modelo devuelve factores de ambos tipos; para HU_IA solo deben pasar los de IA.
    payload = {"datos": 4, "modelo": 3, "evaluacion": 2, "riesgo": 3, "integracion": 2,
               "complejidad": 5, "esfuerzo": 5, "incertidumbre": 5, "rationale": "Datos históricos incompletos."}
    provider = provider_with(payload)
    story = {"code": "PAGO-2", "title": "Clasificar fraude", "type": "HU_IA", "acceptance_criteria": "- [ ] x"}
    output, usage = provider.estimate_story(EstimationInput(story=story))
    assert output.factors == {"datos": 4, "modelo": 3, "evaluacion": 2, "riesgo": 3, "integracion": 2}
    assert output.rationale == "Datos históricos incompletos."
    assert usage.tokens_in == 120


def test_estimation_schema_has_no_length_constraints():
    import json as _json

    from app.agents.providers.adk import _LlmEstimationOutput

    text = _json.dumps(_LlmEstimationOutput.model_json_schema())
    for forbidden in ("minimum", "maximum", "minLength", "maxLength", "pattern"):
        assert forbidden not in text


class ScriptedGemini(BaseLlm):
    """Imita a Gemini con herramientas: el generador consulta a agente_documentos (AgentTool),
    que lista y lee la carpeta; luego el generador responde con set_model_response.

    Cada llamada reporta 10/5 tokens, para comprobar que se suman también las del agente anidado.
    """

    model: str = "fake-gemini-pro"
    calls: list = []

    @staticmethod
    def _responses(llm_request) -> list[dict]:
        return [
            part.function_response.response
            for content in llm_request.contents
            for part in (content.parts or [])
            if part.function_response
        ]

    @staticmethod
    def _call(name: str, args: dict) -> LlmResponse:
        return LlmResponse(content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name=name, args=args))
        ]))

    async def generate_content_async(self, llm_request, stream=False):
        tools = set(llm_request.tools_dict)
        done = self._responses(llm_request)
        if "agente_documentos" in tools:
            self.calls.append(("generador", len(done)))
            if not done:
                response = self._call("agente_documentos", {"request": "¿Qué documentos hay y qué dicen?"})
            else:
                assert "Validar tarjeta" in str(done[0])  # el generador recibe lo que leyó el agente
                response = self._call("set_model_response", {"stories": [
                    {**GENERATION_JSON["stories"][0], "source_document": "reunion.md"}
                ]})
        else:
            assert {"listar_documentos", "leer_documento"} <= tools
            self.calls.append(("documentos", len(done)))
            if not done:
                response = self._call("listar_documentos", {})
            elif len(done) == 1:
                assert done[0]["documentos"][0]["nombre"] == "reunion.md"
                response = self._call("leer_documento", {"nombre": "reunion.md"})
            else:
                response = LlmResponse(content=types.Content(role="model", parts=[
                    types.Part(text=f"reunion.md dice: {done[1]['texto']}")
                ]))
        response.usage_metadata = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5,
        )
        response.turn_complete = True
        yield response


def test_generate_from_documents_uses_document_agent_as_agent_tool(tmp_path):
    from app.agents.document_tools import make_document_tools
    from app.agents.ports import DocumentGenerationInput
    from app.services.documents import FolderSource

    (tmp_path / "PAGO").mkdir()
    (tmp_path / "PAGO" / "reunion.md").write_text("# Pagos\n- Validar tarjeta del cliente\n", encoding="utf-8")
    tools = make_document_tools(FolderSource(tmp_path, "PAGO"), max_total_chars=10_000)
    fake = ScriptedGemini(calls=[])
    provider = AdkProvider(model="gemini-2.5-pro", api_key="clave-falsa-de-prueba", llm=fake)

    output, usage = provider.generate_from_documents(
        DocumentGenerationInput(project_key="PAGO", project_name="Pagos"), tools
    )

    assert [s.title for s in output.stories] == ["Pagar con tarjeta"]
    assert output.stories[0].source_document == "reunion.md"
    assert tools.documents_read == ["reunion.md"]
    assert fake.calls == [("generador", 0), ("documentos", 0), ("documentos", 1), ("documentos", 2), ("generador", 1)]
    # 5 llamadas al modelo: 2 del generador + 3 del agente anidado.
    assert (usage.tokens_in, usage.tokens_out) == (50, 25)
