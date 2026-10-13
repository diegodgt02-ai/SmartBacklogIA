"""Proveedor determinista sin LLM: sirve para demos, tests y para validar el contrato end-to-end."""

from __future__ import annotations

import re

from app.agents.ports import (
    ContextExtractionInput,
    ContextGraphOutput,
    ContextReport,
    DocumentGenerationInput,
    FpaConcept,
    FpaFunction,
    GeneratedStory,
    GenerationInput,
    GenerationOutput,
    RefinementInput,
    RefinementOutput,
    SizingInput,
    SizingOutput,
    Usage,
)

from app.services import context_graph

BULLET = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
# Secciones que describen el documento, no requisitos: no se convierten en HU.
NON_STORY_SECTIONS = {"Procesos", "Participantes", "Decisiones", "Eventos", "Flujo"}
ACTIVITY = re.compile(r"^\[(?P<tipo>[^\]]+)\]\s+(?P<nombre>.+?)(?:\s+\(carril:\s*(?P<carril>[^)]+)\))?(?::\s*(?P<doc>.+))?$")


# Heurística del stub para restricciones: (categoría, severidad, términos que la delatan).
CONSTRAINT_TERMS = (
    ("privacidad", "critica", ("datos personales", "privacidad", "gdpr", "perfil", "comportamiento")),
    ("seguridad", "critica", ("cvv", "número completo", "tarjeta", "cifr", "contraseña", "pci")),
    ("rendimiento", "media", ("segundos", "tiempo de respuesta", "latencia", "rendimiento")),
    ("cumplimiento", "alta", ("normativa", "ley ", "auditor", "regulac")),
    ("integracion", "media", ("pasarela", "límite", "reintentos", "api")),
)
# Una restricción alta o crítica activada pasa a violación si la HU, además, contiene una de estas señales.
VIOLATION_MARKERS = ("api externa", "en la nube", "terceros", "openai", "sin cifrar", "texto plano")


def _story_text(story: dict) -> str:
    return " ".join(str(story.get(k) or "") for k in ("title", "role", "need", "benefit", "acceptance_criteria")).lower()


def _stub_context(story: dict, graph: dict) -> ContextReport:
    """Informe de contexto determinista: coincidencia de palabras con el grafo (solo para demos y pruebas)."""
    text = _story_text(story)
    nodes = context_graph.node_map(graph)

    def mentioned(name: str) -> bool:
        return any(w in text for w in re.findall(r"\w{5,}", name.lower()))

    impacted = [n for n in nodes.values() if n["kind"] == "feature" and mentioned(n["name"])]
    impacted_ids = {n["id"] for n in impacted}
    existing = [n["id"] for n in nodes.values()
                if n["kind"] in ("feature", "entity") and n.get("status") == "existente" and mentioned(n["name"])]
    triggered, violations = [], []
    for c in context_graph.constraints(graph):
        hits = [k for k in c.get("keywords", []) if k in text]
        if hits or set(context_graph.targets_of(graph, c["id"])) & impacted_ids:
            reason = f"La HU menciona {', '.join(hits)}" if hits else "Aplica a una funcionalidad que la HU toca"
            triggered.append({"constraint_id": c["id"], "reason": reason})
            markers = [m for m in VIOLATION_MARKERS if m in text]
            if hits and markers and c["severity"] in ("alta", "critica"):
                violations.append({"constraint_id": c["id"],
                                   "reason": f"{reason} y {', '.join(markers)}: incumple «{c['rule']}»"})
    return ContextReport(
        impacted_features=[{"node_id": n["id"], "how": "Coincidencia de nombre (stub)"} for n in impacted],
        existing_entities=existing, triggered_constraints=triggered, violations=violations,
        summary=f"Stub: {len(impacted)} funcionalidades, {len(triggered)} restricciones activadas, "
                f"{len(violations)} violaciones.",
    )


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

    def generate_from_documents(self, data: DocumentGenerationInput, tools) -> tuple[GenerationOutput, Usage]:
        """Sin LLM: usa las mismas herramientas que el agente de documentos y lee todo, en orden."""
        listing = tools.listar_documentos()
        if listing["status"] != "ok":
            raise RuntimeError(listing["mensaje"])
        stories: list[GeneratedStory] = []
        for doc in listing["documentos"]:
            parts, start = [], 0
            while start is not None:
                chunk = tools.leer_documento(doc["nombre"], desde_caracter=start)
                if chunk["status"] != "ok":
                    break
                parts.append(chunk["texto"])
                start = chunk.get("continuar_desde")
            if not parts:
                continue
            output, _ = self.generate_stories(GenerationInput(
                project_key=data.project_key, project_name=data.project_name,
                source_name=doc["nombre"], text="".join(parts), instructions=data.instructions,
            ))
            stories += [s.model_copy(update={"source_document": doc["nombre"]}) for s in output.stories]
        return GenerationOutput(stories=stories[:200]), Usage(model=self.model)

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

    def size_story(self, data: SizingInput) -> tuple[SizingOutput, Usage]:
        """Heurística determinista de puntos de función. Sirve para demos y tests; el LLM real usa ConceptAct.

        - Una ILF por HU, con el nombre de la épica (o del título): reusada si ya está en el registro.
        - Una transacción por criterio de aceptación: EQ si consulta o muestra, EO si calcula, reporta o
          notifica, EI en otro caso. Sin criterios, una EI por la necesidad de la HU.
        """
        story = data.story
        entity = (story.get("epic") or story.get("title") or "Datos").strip()[:200]
        known = {name.strip().lower() for name in data.registry}
        concepts = [FpaConcept(name=entity, kind="entidad", evidence=story.get("need") or story.get("title"))]
        functions = [FpaFunction(
            name=entity, type="ILF", det=8, ret=1, reused=entity.lower() in known,
            rationale="Entidad de la épica (stub)",
        )]
        criteria = [m.group(1) for line in (story.get("acceptance_criteria") or "").splitlines()
                    if (m := BULLET.match(line))]
        for text in criteria or [story.get("need") or story.get("title") or "Registrar datos"]:
            low = text.lower()
            if any(w in low for w in ("muestra", "consulta", "lista", "visualiza", "busca", "ver ")):
                kind, det, ftr = "EQ", 5, 1
            elif any(w in low for w in ("reporte", "exporta", "envía", "notifica", "calcula", "genera", "confirma")):
                kind, det, ftr = "EO", 6, 2
            else:
                kind, det, ftr = "EI", 5, 1
            name = text.strip()[:200]
            concepts.append(FpaConcept(name=name, kind="transaccion", evidence=text[:500]))
            functions.append(FpaFunction(name=name, type=kind, det=det, ftr=ftr, rationale="Criterio de aceptación (stub)"))
        output = SizingOutput(
            concepts=concepts, functions=functions[:100],
            rationale="Heurística stub: una ILF por épica y una transacción por criterio.",
            context=_stub_context(story, data.context_graph) if data.context_graph else None,
        )
        return output, Usage(model=self.model)

    def extract_context(self, data: ContextExtractionInput, tools) -> tuple[ContextGraphOutput, Usage]:
        """Grafo determinista desde los documentos: actividades BPMN y necesidades → funcionalidades;
        participantes BPMN → entidades; viñetas de secciones «Restricciones» → restricciones."""
        listing = tools.listar_documentos()
        if listing["status"] != "ok":
            raise RuntimeError(listing["mensaje"])
        taken: set[str] = set()
        nodes: list[dict] = []
        lines_all: list[tuple[str, str, str]] = []  # (documento, sección, línea)
        for doc in listing["documentos"]:
            parts, start = [], 0
            while start is not None:
                chunk = tools.leer_documento(doc["nombre"], desde_caracter=start)
                if chunk["status"] != "ok":
                    break
                parts.append(chunk["texto"])
                start = chunk.get("continuar_desde")
            section = ""
            for raw in "".join(parts).splitlines():
                if raw.startswith("#"):
                    section = raw.lstrip("#").strip()
                elif m := BULLET.match(raw):
                    lines_all.append((doc["nombre"], section, m.group(1)))
        corpus = " ".join(line.lower() for _, _, line in lines_all)
        for docname, section, line in lines_all:
            low_section = section.lower()
            source = f"{docname} · {section}"[:300]
            if low_section == "participantes":
                first = line.split()[0].lower()
                status = "existente" if f"{first} actual" in corpus else "planificado"
                nodes.append({"id": context_graph.slugify(line, taken), "kind": "entity", "name": line[:200],
                              "status": status, "source": source})
            elif low_section == "actividades" and (act := ACTIVITY.match(line)):
                name = act.group("nombre")
                nodes.append({"id": context_graph.slugify(name, taken), "kind": "feature", "name": name[:200],
                              "status": "planificado", "source": source})
            elif low_section.startswith("necesidad"):
                nodes.append({"id": context_graph.slugify(line[:60], taken), "kind": "feature", "name": line[:200],
                              "status": "planificado", "source": source})
            elif "restricci" in low_section:
                low = line.lower()
                category, severity, terms = next(
                    ((c, s, [t.strip() for t in ts if t in low]) for c, s, ts in CONSTRAINT_TERMS
                     if any(t in low for t in ts)), ("otro", "baja", []))
                nodes.append({"id": context_graph.slugify(line[:50], taken), "kind": "constraint",
                              "name": line[:120], "category": category, "severity": severity, "rule": line[:1000],
                              "keywords": terms, "source": source})
        entities = [n for n in nodes if n["kind"] == "entity"]
        edges = [{"source": f["id"], "target": e["id"], "type": "uses"}
                 for f in nodes if f["kind"] == "feature"
                 for e in entities if e["name"].split()[0].lower() in f["name"].lower()]
        output = ContextGraphOutput(nodes=nodes[:300], edges=edges[:1000],
                                    notes="Stub: grafo por heurística de secciones; revísalo antes de aprobar.")
        return output, Usage(model=self.model)
