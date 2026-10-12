"""Proveedor determinista sin LLM: sirve para demos, tests y para validar el contrato end-to-end."""

from __future__ import annotations

import re

from app.agents.ports import (
    EstimationInput,
    EstimationOutput,
    GeneratedStory,
    GenerationInput,
    GenerationOutput,
    RefinementInput,
    RefinementOutput,
    Usage,
)

BULLET = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
# Secciones que describen el documento, no requisitos: no se convierten en HU.
NON_STORY_SECTIONS = {"Procesos", "Participantes", "Decisiones", "Eventos", "Flujo"}
ACTIVITY = re.compile(r"^\[(?P<tipo>[^\]]+)\]\s+(?P<nombre>.+?)(?:\s+\(carril:\s*(?P<carril>[^)]+)\))?(?::\s*(?P<doc>.+))?$")


class StubProvider:
    name = "stub"
    model = "stub-determinista"

    def generate_stories(self, data: GenerationInput) -> tuple[GenerationOutput, Usage]:
        stories: list[GeneratedStory] = []
        epic: str | None = None
        section = ""
        for raw in data.text.splitlines():
            line = raw.rstrip()
            if line.startswith("# ") and not line.startswith("# Proceso BPMN"):
                epic = line[2:].strip()[:255]
                continue
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            match = BULLET.match(line)
            if not match:
                continue
            text = match.group(1)
            if section == "Actividades":
                act = ACTIVITY.match(text)
                if act:
                    name = act.group("nombre")
                    doc = act.group("doc")
                    stories.append(GeneratedStory(
                        epic=epic or data.source_name,
                        title=name[:255] if len(name) >= 3 else f"Actividad: {name}"[:255],
                        role=act.group("carril"),
                        need=f"ejecutar la actividad «{name}»",
                        acceptance_criteria=f"- [ ] La actividad «{name}» se ejecuta según el proceso" + (f"\n- [ ] {doc}" if doc else ""),
                        type="HU_TRADICIONAL",
                        type_justification="Actividad de proceso sin componente de IA (stub)",
                        source_section=f"Actividad: {name}",
                    ))
                    continue
            if section == "Procesos":
                epic = text[:255]
                continue
            if section in NON_STORY_SECTIONS:
                continue
            if len(text) >= 8:
                stories.append(GeneratedStory(
                    epic=epic,
                    title=text[:255],
                    acceptance_criteria=f"- [ ] {text}",
                    type="HU_TRADICIONAL",
                    type_justification="Clasificación por defecto del stub",
                    source_section=section or None,
                ))
        return GenerationOutput(stories=stories), Usage(model=self.model)

    def refine_story(self, data: RefinementInput) -> tuple[RefinementOutput, Usage]:
        current = data.story.get("acceptance_criteria") or ""
        lines = []
        for raw in current.splitlines():
            if not raw.strip():
                continue
            text = BULLET.match(raw)
            body = text.group(1) if text else raw.strip()
            lines.append(f"- [ ] {body}")
        if not lines:
            lines = ["- [ ] Pendiente de definir criterios de aceptación"]
        output = RefinementOutput(
            acceptance_criteria="\n".join(lines),
            notes="Normalizado por el proveedor stub (sin LLM): formato checklist.",
        )
        return output, Usage(model=self.model)

    def estimate_story(self, data: EstimationInput) -> tuple[EstimationOutput, Usage]:
        """Heurística por palabras clave. Sirve para demos y tests; el LLM real usa ESTIMATOR_INSTRUCTION."""
        story = data.story
        text = " ".join(str(story.get(k) or "") for k in ("title", "need", "acceptance_criteria")).lower()
        has = lambda *words: any(w in text for w in words)  # noqa: E731
        if story.get("type") == "HU_IA":
            factors = {
                "datos": 4 if has("dato", "histórico", "dataset") else 2,
                "modelo": 3 if has("clasific", "predic", "llm", "modelo", "prompt", "extrae") else 2,
                "evaluacion": 3 if has("precisi", "métrica", "evalu") else 2,
                "riesgo": 3 if has("ambig", "alucin", "sesgo") else 2,
                "integracion": 2,
            }
            rationale = "Heurística stub: factores por palabras clave del texto de la HU."
        else:
            factors = {
                "complejidad": 3 if len(story.get("acceptance_criteria") or "") > 300 else 2,
                "esfuerzo": 3 if has("integra", "pasarela", "api") else 2,
                "incertidumbre": 4 if has("por definir", "ambig") else 2,
            }
            rationale = "Heurística stub: factores según longitud de criterios y palabras clave."
        return EstimationOutput(factors=factors, rationale=rationale), Usage(model=self.model)
