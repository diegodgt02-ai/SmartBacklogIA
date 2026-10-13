"""Orquesta las ejecuciones de agentes y persiste sus resultados con los servicios del dominio.

Reglas que este módulo hace cumplir (no el proveedor):
- La generación solo crea HU en `borrador`, con `external_id` determinista (reprocesar no duplica).
- El refinamiento pasa por el mismo flujo que un ADK externo: `claim` → `submit_result`,
  y termina en `refinada`. Nunca aprueba etapas HITL ni mueve HU a `lista`.
- Cada ejecución queda en `agent_runs` (estado, error, tokens, costo).
- Una falla no deja HU a medias: la generación se hace en una sola transacción.
- La estimación solo puntúa factores (el LLM); los puntos los calcula `services/estimation.py`.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Callable

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.document_tools import make_document_tools
from app.agents.ports import (
    AgentNotConfigured,
    DocumentGenerationInput,
    EstimationInput,
    GeneratedStory,
    GenerationInput,
    RefinementInput,
)
from app.agents.registry import get_provider
from app.config import settings
from app.db import SessionLocal
from app.models import AgentRun, Project, RefinementRequest, UserStory, utcnow
from app.schemas import RefinementResult
from app.services import audit
from app.services import refinement as refinement_service
from app.services import stories as story_service
from app.services.documents import get_document_source
from app.services.errors import Conflict, NotFound, ServiceUnavailable, Unprocessable
from app.services.workflow import Actor

log = logging.getLogger(__name__)


@dataclass
class ServiceActor:
    """Actor de los agentes: se registra como servicio en el historial."""

    provider_name: str

    def as_actor(self) -> Actor:
        return Actor(kind="service", name=f"agent:{self.provider_name}", project_role="service")


def external_id_for(source_name: str, title: str) -> str:
    digest = hashlib.sha1(f"{source_name}|{title.strip().lower()}".encode()).hexdigest()[:12]
    return f"gen-{digest}"


def _cost(tokens_in: int, tokens_out: int) -> float | None:
    if settings.agent_price_in_per_1m is None or settings.agent_price_out_per_1m is None:
        return None
    return round((tokens_in * settings.agent_price_in_per_1m + tokens_out * settings.agent_price_out_per_1m) / 1_000_000, 6)


def run_out(run: AgentRun) -> dict:
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "provider": run.provider,
        "model": run.model,
        "story_id": run.story_id,
        "input_ref": run.input_ref,
        "input_chars": run.input_chars,
        "result": run.result,
        "error": run.error,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "cost_usd": run.cost_usd,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _dispatch(background: BackgroundTasks | None, fn: Callable, *args) -> None:
    """Ejecuta ahora (inline, tests) o en segundo plano (producción). Mover a un worker = cambiar esto."""
    if settings.agent_execution == "inline" or background is None:
        fn(*args)
    else:
        background.add_task(fn, *args)


def _new_run(db: Session, project: Project, kind: str, provider_name: str, model: str | None, **fields) -> AgentRun:
    run = AgentRun(
        project_id=project.id,
        kind=kind,
        status="queued",
        provider=provider_name,
        model=model,
        **fields,
    )
    db.add(run)
    db.commit()
    return run


# ---------- generación de HU desde documentos ----------

def start_generation(
    db: Session,
    project: Project,
    *,
    source_name: str,
    text: str,
    instructions: str | None,
    requested_by_id: int | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    provider_name, model = _provider_identity()
    run = _new_run(
        db, project, "generate_stories", provider_name, model,
        input_ref=source_name, input_chars=len(text), requested_by_id=requested_by_id,
    )
    run_id = run.id
    _dispatch(background, execute_generation, run_id, project.key, source_name, text, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_generation(run_id: int, project_key: str, source_name: str, text: str, instructions: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        project = db.scalar(select(Project).where(Project.key == project_key))
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            output, usage = provider.generate_stories(
                GenerationInput(
                    project_key=project.key, project_name=project.name,
                    source_name=source_name, text=text, instructions=instructions,
                )
            )
            actor = ServiceActor(provider.name).as_actor()
            created, updated = _persist_stories(db, project, output.stories, actor, source_name)
            run.result = {"created": created, "updated": updated, "total": len(output.stories)}
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del proveedor queda registrado, sin HU a medias
            db.rollback()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Generación %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


def _persist_stories(
    db: Session, project: Project, items: list[GeneratedStory], actor: Actor, default_source: str,
    *, per_item_source: bool = False,
) -> tuple[list[str], list[str]]:
    """Crea o actualiza las HU generadas (en borrador). La clave de idempotencia es documento + título.

    Con un solo documento, la fuente es siempre su nombre (aunque el modelo llene source_document),
    así reprocesar el mismo archivo sigue sin duplicar.
    """
    created, updated = [], []
    for item in items:
        source = (item.source_document if per_item_source else None) or default_source
        data = item.model_dump(exclude_none=True, exclude={"source_section", "source_document"})
        data["source_ref"] = f"{source} · {item.source_section}" if item.source_section else source
        story, is_new = story_service.upsert_story(
            db, project, data, actor, external_id=external_id_for(source, item.title)
        )
        (created if is_new else updated).append(story.code)
    return created, updated


# ---------- generación de HU desde todos los documentos (agente de documentos como AgentTool) ----------

def start_generation_from_documents(
    db: Session,
    project: Project,
    *,
    instructions: str | None,
    requested_by_id: int | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    provider_name, model = _provider_identity()
    # Falla aquí (antes de crear el run) si la fuente no está configurada o el bucket no responde.
    source = get_document_source(project.key)
    if not source.list():
        raise Unprocessable(f"No hay documentos en {source.location}")
    run = _new_run(
        db, project, "generate_from_docs", provider_name, model,
        input_ref=f"documentos: {source.location}"[:500], requested_by_id=requested_by_id,
    )
    run_id = run.id
    _dispatch(background, execute_generation_from_documents, run_id, project.key, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_generation_from_documents(run_id: int, project_key: str, instructions: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        project = db.scalar(select(Project).where(Project.key == project_key))
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            tools = make_document_tools(get_document_source(project.key), settings.agent_max_doc_chars)
            output, usage = provider.generate_from_documents(
                DocumentGenerationInput(project_key=project.key, project_name=project.name, instructions=instructions),
                tools,
            )
            actor = ServiceActor(provider.name).as_actor()
            created, updated = _persist_stories(
                db, project, output.stories, actor, "documentos", per_item_source=True
            )
            run.result = {
                "created": created, "updated": updated, "total": len(output.stories),
                "documents_read": tools.documents_read,
            }
            run.input_chars = tools.chars_served
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
        except Exception as exc:  # noqa: BLE001 - igual que execute_generation: sin HU a medias
            db.rollback()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Generación desde documentos %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


# ---------- refinamiento de una HU ----------

def start_refinement(
    db: Session,
    project: Project,
    story: UserStory,
    *,
    actor: Actor,
    user_id: int | None,
    instructions: str | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    """Lleva la HU a en_refinamiento (si no lo está) y encola el refinamiento por agente."""
    if story.status != "en_refinamiento":
        req = story_service.transition(db, story, "en_refinamiento", actor, user_id=user_id)
    else:
        req = next((r for r in story.refinements if r.status == "pending"), None)
        if req is None:
            raise Conflict("La HU está en refinamiento sin solicitud pendiente")
    db.commit()
    return enqueue_refinement(db, project, story, req.id, instructions, user_id, background)


def enqueue_refinement(
    db: Session,
    project: Project,
    story: UserStory,
    request_id: int,
    instructions: str | None,
    user_id: int | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    provider_name, model = _provider_identity()
    run = _new_run(
        db, project, "refine_story", provider_name, model,
        story_id=story.id, input_ref=story.code, input_chars=len(story.acceptance_criteria or ""),
        requested_by_id=user_id,
    )
    run_id = run.id
    _dispatch(background, execute_refinement, run_id, request_id, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_refinement(run_id: int, request_id: int, instructions: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            req = refinement_service.get_request(db, request_id)
            if req.status != "pending":
                raise Conflict(f"La solicitud {request_id} ya está en estado '{req.status}'")
            claimer = f"agent:{provider.name}"
            refinement_service.claim(db, request_id, claimer)
            req = refinement_service.get_request(db, request_id)
            snapshot = req.input_snapshot

            output, usage = provider.refine_story(RefinementInput(story=snapshot, instructions=instructions))
            result = RefinementResult(
                expected_version=snapshot["version"],
                **output.model_dump(exclude={"notes"}),
                notes=output.notes,
            )
            refinement_service.submit_result(db, request_id, result, claimer)

            run = db.get(AgentRun, run_id)
            run.result = {"request_id": request_id, "story": snapshot["code"], "notes": output.notes}
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            req = db.get(RefinementRequest, request_id)
            if req is not None and req.status == "in_progress":
                req.status, req.error = "failed", _error_text(exc)
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Refinamiento %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


def auto_refine_if_enabled(db: Session, project: Project, request, user_id: int | None, background: BackgroundTasks | None) -> None:
    """Con AGENT_AUTO_REFINE=true, una HU que entra a refinamiento la toma el agente interno."""
    if not settings.agent_auto_refine or request is None:
        return
    try:
        enqueue_refinement(db, project, request.story, request.id, None, user_id, background)
    except ServiceUnavailable:
        # El cambio de estado ya se guardó; sin proveedor la HU queda pendiente para el flujo manual.
        log.warning("Auto-refinamiento omitido: proveedor de IA no configurado")


# ---------- estimación de una HU (etapa 3) ----------

def start_estimation(
    db: Session,
    project: Project,
    story: UserStory,
    *,
    user_id: int | None,
    instructions: str | None,
    background: BackgroundTasks | None,
) -> AgentRun:
    if story.type not in ("HU_IA", "HU_TRADICIONAL"):
        raise Unprocessable("La HU necesita tipo HU_IA o HU_TRADICIONAL antes de estimarse")
    provider_name, model = _provider_identity()
    run = _new_run(
        db, project, "estimate_story", provider_name, model,
        story_id=story.id, input_ref=story.code, input_chars=len(story.acceptance_criteria or ""),
        requested_by_id=user_id,
    )
    run_id = run.id
    _dispatch(background, execute_estimation, run_id, project.key, story.code, instructions)
    db.expire_all()
    return db.get(AgentRun, run_id)


def execute_estimation(run_id: int, project_key: str, story_code: str, instructions: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        run.status, run.started_at = "running", utcnow()
        db.commit()
        try:
            provider = get_provider()
            project = db.scalar(select(Project).where(Project.key == project_key))
            story = story_service.get_story(db, project, story_code)
            output, usage = provider.estimate_story(
                EstimationInput(story=story_service.snapshot(story), instructions=instructions)
            )
            actor = ServiceActor(provider.name).as_actor()
            # Factores → puntos en el servicio de HU: valida tipo, rango y método, y deja diff en el historial.
            diff = story_service.apply_changes(db, project, story, {"estimate_factors": output.factors}, actor)
            if output.rationale:
                audit.record(db, story, actor, "estimation_rationale", diff={"rationale": output.rationale})
            run = db.get(AgentRun, run_id)
            run.result = {
                "story": story.code,
                "points": story.estimate_points,
                "method": story.estimate_method,
                "changed": sorted(diff),
                "rationale": output.rationale,
            }
            run.model, run.tokens_in, run.tokens_out = usage.model, usage.tokens_in, usage.tokens_out
            run.cost_usd = _cost(usage.tokens_in, usage.tokens_out)
            run.status = "succeeded"
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(AgentRun, run_id)
            run.status, run.error = "failed", _error_text(exc)
            db.commit()
            log.warning("Estimación %s falló: %s", run_id, exc)
        finally:
            run = db.get(AgentRun, run_id)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


# ---------- helpers ----------

def _provider_identity() -> tuple[str, str | None]:
    """Nombre y modelo sin llamar al proveedor. Sin proveedor configurado responde 503 antes de crear el run."""
    try:
        provider = get_provider()
    except AgentNotConfigured as exc:
        raise ServiceUnavailable(str(exc)) from exc
    return provider.name, provider.model


def _error_text(exc: Exception) -> str:
    text = f"{exc.__class__.__name__}: {exc}" if not isinstance(exc, (Conflict, NotFound)) else str(exc)
    return text[:2000]


__all__ = [
    "AgentNotConfigured",
    "auto_refine_if_enabled",
    "execute_estimation",
    "execute_generation",
    "execute_generation_from_documents",
    "execute_refinement",
    "external_id_for",
    "run_out",
    "start_estimation",
    "start_generation",
    "start_generation_from_documents",
    "start_refinement",
]
