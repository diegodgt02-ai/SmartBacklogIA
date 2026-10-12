"""Contrato entre el backend y cualquier proveedor de IA (ADK, stub, ...).

Los proveedores devuelven DTOs validados por Pydantic. Nunca escriben en la base:
quien persiste es `AgentRunner`, que aplica las reglas de HITL y de estados.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field


class AgentNotConfigured(Exception):
    """El proveedor configurado no puede usarse (falta clave, paquete o proveedor 'none')."""


class GeneratedStory(BaseModel):
    epic: str | None = Field(None, max_length=255, description="Épica a la que pertenece la HU")
    title: str = Field(min_length=3, max_length=255)
    role: str | None = Field(None, description="Rol: 'como <rol>'")
    need: str | None = Field(None, description="Necesidad: 'quiero <necesidad>'")
    benefit: str | None = Field(None, description="Beneficio: 'para <beneficio>'")
    acceptance_criteria: str | None = Field(None, max_length=20000, description="Criterios en markdown: checklist '- [ ]' o Gherkin")
    type: Literal["HU_IA", "HU_TRADICIONAL"] | None = None
    type_justification: str | None = None
    source_section: str | None = Field(None, description="Página, sección o proceso del documento de origen")


class GenerationOutput(BaseModel):
    stories: list[GeneratedStory] = Field(default_factory=list, max_length=200)


class RefinementOutput(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=255)
    role: str | None = None
    need: str | None = None
    benefit: str | None = None
    acceptance_criteria: str | None = Field(None, max_length=20000)
    notes: str | None = None


class GenerationInput(BaseModel):
    project_key: str
    project_name: str
    source_name: str
    text: str
    instructions: str | None = None


class RefinementInput(BaseModel):
    story: dict
    instructions: str | None = None


class EstimationOutput(BaseModel):
    factors: dict[str, int] = Field(description="Factores 1–5 del método del tipo de HU; el backend calcula los puntos")
    rationale: str | None = Field(None, max_length=2000, description="Por qué se puntuó así, en una o dos frases")


class EstimationInput(BaseModel):
    story: dict
    instructions: str | None = None


class Usage(BaseModel):
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class AgentProvider(Protocol):
    name: str
    model: str | None

    def generate_stories(self, data: GenerationInput) -> tuple[GenerationOutput, Usage]: ...

    def refine_story(self, data: RefinementInput) -> tuple[RefinementOutput, Usage]: ...

    def estimate_story(self, data: EstimationInput) -> tuple[EstimationOutput, Usage]: ...
