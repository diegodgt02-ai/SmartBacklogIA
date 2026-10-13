"""Contrato entre el backend y cualquier proveedor de IA (ADK, stub, ...).

Los proveedores devuelven DTOs validados por Pydantic. Nunca escriben en la base:
quien persiste es `AgentRunner`, que aplica las reglas de HITL y de estados.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.agents.document_tools import DocumentTools


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
    source_document: str | None = Field(None, max_length=255, description="Documento de origen (generación desde varios documentos)")


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


class DocumentGenerationInput(BaseModel):
    """Generación desde todos los documentos del proyecto: el agente decide qué leer con sus herramientas."""

    project_key: str
    project_name: str
    instructions: str | None = None


class RefinementInput(BaseModel):
    story: dict
    instructions: str | None = None


class FpaConcept(BaseModel):
    """Paso «Concept» (ConceptAct): qué entidades y transacciones describe la HU, con evidencia."""

    name: str = Field(min_length=1, max_length=200)
    kind: Literal["entidad", "transaccion"]
    evidence: str | None = Field(None, max_length=500, description="Cita o paráfrasis de la HU que lo respalda")


class FpaFunction(BaseModel):
    """Paso «Act»: una función IFPUG con sus conteos. El backend calcula complejidad y puntos."""

    name: str = Field(min_length=1, max_length=200)
    type: Literal["ILF", "EIF", "EI", "EO", "EQ"]
    det: int = Field(ge=1)
    ret: int | None = Field(None, ge=1, description="Solo ILF/EIF")
    ftr: int | None = Field(None, ge=0, description="Solo EI/EO/EQ")
    reused: bool = False
    rationale: str | None = Field(None, max_length=500)


class ImpactedFeature(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    how: str | None = Field(None, max_length=300)


class ConstraintHit(BaseModel):
    constraint_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(None, max_length=500)


class ContextReport(BaseModel):
    """Informe del agente de contexto: cómo se relaciona la HU con el grafo aprobado del proyecto."""

    impacted_features: list[ImpactedFeature] = Field(default_factory=list, max_length=100)
    existing_entities: list[str] = Field(default_factory=list, max_length=100, description="ids de entidades o funcionalidades existentes que la HU reusa")
    triggered_constraints: list[ConstraintHit] = Field(default_factory=list, max_length=100)
    violations: list[ConstraintHit] = Field(default_factory=list, max_length=100)
    summary: str | None = Field(None, max_length=2000)


class SizingInput(BaseModel):
    story: dict
    registry: list[str] = Field(default_factory=list, description="ILF/EIF ya contados por HU anteriores")
    instructions: str | None = None
    context_graph: dict | None = Field(None, description="Grafo de contexto aprobado: {version, nodes, edges}")


class SizingOutput(BaseModel):
    concepts: list[FpaConcept] = Field(default_factory=list, max_length=200)
    functions: list[FpaFunction] = Field(default_factory=list, max_length=100)
    rationale: str | None = Field(None, max_length=2000)
    context: ContextReport | None = None

    def components(self) -> list[dict]:
        """Componentes para services/fpa.py (sin campos vacíos)."""
        return [f.model_dump(exclude_none=True) for f in self.functions]


class GraphNode(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["feature", "entity", "constraint"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    status: Literal["existente", "planificado"] | None = None
    category: str | None = None
    severity: Literal["baja", "media", "alta", "critica"] | None = None
    rule: str | None = Field(None, max_length=1000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    source: str | None = Field(None, max_length=300)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: Literal["depends_on", "uses", "applies_to"]


class ContextGraphOutput(BaseModel):
    """Grafo propuesto por el agente. services/context_graph.py lo valida antes de guardarlo como borrador."""

    nodes: list[GraphNode] = Field(default_factory=list, max_length=300)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=1000)
    notes: str | None = Field(None, max_length=2000)

    def as_graph(self) -> tuple[list[dict], list[dict]]:
        return [n.model_dump(exclude_none=True) for n in self.nodes], [e.model_dump() for e in self.edges]


class ContextExtractionInput(BaseModel):
    project_key: str
    project_name: str
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

    def size_story(self, data: SizingInput) -> tuple[SizingOutput, Usage]: ...

    def extract_context(
        self, data: ContextExtractionInput, tools: "DocumentTools"
    ) -> tuple[ContextGraphOutput, Usage]: ...

    def generate_from_documents(
        self, data: DocumentGenerationInput, tools: "DocumentTools"
    ) -> tuple[GenerationOutput, Usage]: ...
