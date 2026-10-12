"""Proveedor Google ADK con Gemini. ADK se importa aquí, así que el backend arranca aunque falle.

Cada llamada crea un LlmAgent con output_schema (salida JSON validada por Pydantic) y una
sesión efímera en memoria. No se guarda estado entre llamadas ni se usan herramientas: el
agente solo devuelve datos; quien escribe en la base es AgentRunner.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import uuid

from typing import Literal

from pydantic import BaseModel, Field

from app.agents.ports import (
    AgentNotConfigured,
    EstimationInput,
    EstimationOutput,
    GenerationInput,
    GenerationOutput,
    RefinementInput,
    RefinementOutput,
    Usage,
)
from app.agents.prompts import ESTIMATOR_INSTRUCTION, GENERATOR_INSTRUCTION, REFINER_INSTRUCTION


# Esquemas que ve el modelo. Gemini rechaza restricciones como minLength/maxLength/maxItems
# (400 INVALID_ARGUMENT), así que aquí van sin longitudes. Las reglas de negocio se aplican
# después, validando la respuesta con los DTO estrictos de ports.py.
IA_FACTORS = ("datos", "modelo", "evaluacion", "riesgo", "integracion")
TRADITIONAL_FACTORS = ("complejidad", "esfuerzo", "incertidumbre")


class _LlmStory(BaseModel):
    epic: str | None = Field(None, description="Épica a la que pertenece la HU")
    title: str = Field(description="Título corto de la HU")
    role: str | None = Field(None, description="Rol: 'como <rol>'")
    need: str | None = Field(None, description="Necesidad: 'quiero <necesidad>'")
    benefit: str | None = Field(None, description="Beneficio: 'para <beneficio>'")
    acceptance_criteria: str | None = Field(None, description="Criterios en markdown: checklist '- [ ]' o Gherkin")
    type: Literal["HU_IA", "HU_TRADICIONAL"] | None = Field(None, description="Tipo de HU")
    type_justification: str | None = Field(None, description="Justificación del tipo")
    source_section: str | None = Field(None, description="Página, sección o actividad del documento de origen")


class _LlmGenerationOutput(BaseModel):
    stories: list[_LlmStory] = Field(default_factory=list, description="HU propuestas")


class _LlmRefinementOutput(BaseModel):
    title: str | None = Field(None, description="Título refinado o el original")
    role: str | None = None
    need: str | None = None
    benefit: str | None = None
    acceptance_criteria: str | None = Field(None, description="Criterios en markdown (checklist)")
    notes: str | None = Field(None, description="Qué se cambió y por qué, en una o dos frases")


class _LlmEstimationOutput(BaseModel):
    # Un campo por factor (Gemini no admite dict libre). Solo se usan los del tipo de la HU.
    datos: int | None = Field(None, description="Factor HU-IA: disponibilidad y calidad de datos, 1–5")
    modelo: int | None = Field(None, description="Factor HU-IA: complejidad del modelo o prompt, 1–5")
    evaluacion: int | None = Field(None, description="Factor HU-IA: necesidad de evaluación, 1–5")
    riesgo: int | None = Field(None, description="Factor HU-IA y HU-Tradicional: riesgo o incertidumbre de resultado, 1–5")
    integracion: int | None = Field(None, description="Factor HU-IA: trabajo tradicional alrededor, 1–5")
    complejidad: int | None = Field(None, description="Factor HU-Tradicional: complejidad de reglas y flujos, 1–5")
    esfuerzo: int | None = Field(None, description="Factor HU-Tradicional: esfuerzo de construcción e integración, 1–5")
    incertidumbre: int | None = Field(None, description="Factor HU-Tradicional: claridad del requisito, 1–5")
    rationale: str | None = Field(None, description="Justificación en una o dos frases")


class AdkProvider:
    name = "adk"

    def __init__(self, model: str, api_key: str | None, llm=None):
        """`llm` permite inyectar un BaseLlm (pruebas sin red). En producción se usa el nombre de Gemini."""
        try:
            from google.adk.agents import LlmAgent
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise AgentNotConfigured("google-adk no está instalado (revisa requirements.txt)") from exc
        if not api_key:
            raise AgentNotConfigured("Falta GOOGLE_API_KEY (clave de Gemini API, Google AI Studio o cuenta empresarial)")
        # google-genai lee estas variables del entorno del proceso, no del archivo .env de pydantic.
        os.environ.setdefault("GOOGLE_API_KEY", api_key)
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
        self._model_ref = llm if llm is not None else model
        self.model = model if isinstance(model, str) else getattr(model, "model", None)
        self._LlmAgent = LlmAgent
        self._Runner = Runner
        self._sessions_cls = InMemorySessionService
        self._types = types

    def _call(self, instruction: str, llm_schema: type[BaseModel], schema: type[BaseModel], user_text: str):
        agent = self._LlmAgent(
            name="spb_agent",
            model=self._model_ref,
            instruction=instruction,
            output_schema=llm_schema,
        )
        sessions = self._sessions_cls()
        runner = self._Runner(app_name="spb", agent=agent, session_service=sessions)

        async def _run():
            session_id = uuid.uuid4().hex
            created = sessions.create_session(app_name="spb", user_id="spb", session_id=session_id)
            if inspect.isawaitable(created):
                await created
            message = self._types.Content(role="user", parts=[self._types.Part(text=user_text)])
            usage = Usage(model=self.model)
            final_text = None
            async for event in runner.run_async(user_id="spb", session_id=session_id, new_message=message):
                meta = event.usage_metadata
                if meta is not None:
                    usage.tokens_in += meta.prompt_token_count or 0
                    usage.tokens_out += meta.candidates_token_count or 0
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = "".join(part.text or "" for part in event.content.parts)
            if not final_text:
                raise RuntimeError("El modelo no devolvió una respuesta final")
            # Validación estricta del lado del backend (longitudes, enums, límites).
            return schema.model_validate_json(final_text), usage

        return asyncio.run(_run())

    def generate_stories(self, data: GenerationInput) -> tuple[GenerationOutput, Usage]:
        user_text = (
            f"Proyecto: {data.project_key} — {data.project_name}\n"
            f"Fuente: {data.source_name}\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
            + f"\n<documento>\n{data.text}\n</documento>"
        )
        return self._call(GENERATOR_INSTRUCTION, _LlmGenerationOutput, GenerationOutput, user_text)

    def refine_story(self, data: RefinementInput) -> tuple[RefinementOutput, Usage]:
        story = data.story
        user_text = (
            f"HU {story.get('code')} (épica: {story.get('epic') or 'sin épica'}, tipo: {story.get('type') or 'sin tipo'})\n"
            f"Título: {story.get('title')}\n"
            f"Rol: {story.get('role') or ''}\n"
            f"Necesidad: {story.get('need') or ''}\n"
            f"Beneficio: {story.get('benefit') or ''}\n"
            f"Criterios actuales (markdown):\n<criterios>\n{story.get('acceptance_criteria') or ''}\n</criterios>\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
        )
        return self._call(REFINER_INSTRUCTION, _LlmRefinementOutput, RefinementOutput, user_text)

    def estimate_story(self, data: EstimationInput) -> tuple[EstimationOutput, Usage]:
        story = data.story
        user_text = (
            f"HU {story.get('code')} (tipo: {story.get('type') or 'sin tipo'})\n"
            f"Título: {story.get('title')}\n"
            f"Rol: {story.get('role') or ''}\n"
            f"Necesidad: {story.get('need') or ''}\n"
            f"Beneficio: {story.get('benefit') or ''}\n"
            f"Criterios (markdown):\n<criterios>\n{story.get('acceptance_criteria') or ''}\n</criterios>\n"
            + (f"Instrucciones adicionales: {data.instructions}\n" if data.instructions else "")
        )
        llm_out, usage = self._call(ESTIMATOR_INSTRUCTION, _LlmEstimationOutput, _LlmEstimationOutput, user_text)
        # Solo pasan los factores del tipo de la HU; los demás se descartan aquí.
        keys = IA_FACTORS if story.get("type") == "HU_IA" else TRADITIONAL_FACTORS
        factors = {k: getattr(llm_out, k) for k in keys if getattr(llm_out, k) is not None}
        return EstimationOutput(factors=factors, rationale=llm_out.rationale), usage
