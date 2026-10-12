from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Epic, Project, RefinementRequest, UserStory
from app.services import audit, estimation
from app.services.errors import NotFound, Unprocessable
from app.services.workflow import Actor, check_transition

EDITABLE_FIELDS = (
    "title",
    "role",
    "need",
    "benefit",
    "acceptance_criteria",
    "type",
    "type_justification",
    "estimate_points",
    "estimate_method",
    "estimate_factors",
    "priority_rank",
    "priority_method",
    "source_ref",
)


def next_code(project: Project) -> str:
    project.story_seq += 1
    return f"{project.key}-{project.story_seq}"


def get_story(db: Session, project: Project, code: str) -> UserStory:
    story = db.scalar(
        select(UserStory).where(UserStory.project_id == project.id, UserStory.code == code)
    )
    if story is None:
        raise NotFound(f"HU {code} no existe en {project.key}")
    return story


def get_or_create_epic(db: Session, project: Project, title: str, description: str | None = None) -> Epic:
    epic = db.scalar(select(Epic).where(Epic.project_id == project.id, Epic.title == title))
    if epic is None:
        epic = Epic(project=project, title=title, description=description, position=len(project.epics))
        db.add(epic)
        db.flush()
    return epic


def snapshot(story: UserStory) -> dict:
    return {
        "code": story.code,
        "epic": story.epic.title if story.epic else None,
        "title": story.title,
        "role": story.role,
        "need": story.need,
        "benefit": story.benefit,
        "acceptance_criteria": story.acceptance_criteria,
        "type": story.type,
        "type_justification": story.type_justification,
        "estimate_points": story.estimate_points,
        "estimate_method": story.estimate_method,
        "estimate_factors": story.estimate_factors,
        "version": story.version,
    }


def _with_estimate(data: dict, story_type: str | None) -> dict:
    """Si llegan factores de estimación, calcula puntos y método en el servidor (ver services/estimation.py)."""
    try:
        return estimation.apply_to_story_data(story_type, dict(data))
    except estimation.EstimationError as exc:
        raise Unprocessable(str(exc)) from exc


def _resolve_assignee(project: Project, data: dict) -> None:
    """Convierte assignee_email (miembro del proyecto) en assignee_id."""
    if "assignee_email" not in data:
        return
    email = data.pop("assignee_email")
    if not email:
        data["assignee_id"] = None
        return
    member = next((m for m in project.members if m.user.email == email.lower()), None)
    if member is None:
        raise Unprocessable(f"{email} no es miembro del proyecto {project.key}")
    data["assignee_id"] = member.user_id


def apply_changes(
    db: Session,
    project: Project,
    story: UserStory,
    data: dict,
    actor: Actor,
    bump_version: bool = True,
) -> dict:
    """Aplica cambios editables y registra el diff. `epic` (título) es None para quitar la épica."""
    data = _with_estimate(data, data.get("type", story.type))
    if "epic" in data:
        title = data.pop("epic")
        data["epic_id"] = get_or_create_epic(db, project, title).id if title else None
    _resolve_assignee(project, data)
    fields = {k: v for k, v in data.items() if k in EDITABLE_FIELDS or k in ("epic_id", "assignee_id")}
    diff = {k: v for k, v in fields.items() if getattr(story, k) != v}
    if diff:
        for key, value in diff.items():
            setattr(story, key, value)
        if bump_version:
            story.version += 1
        audit.record(db, story, actor, "updated", diff=diff)
    return diff


def create_story(
    db: Session,
    project: Project,
    data: dict,
    actor: Actor,
    external_id: str | None = None,
) -> UserStory:
    data = _with_estimate(data, data.get("type"))
    epic_title = data.pop("epic", None)
    _resolve_assignee(project, data)
    story = UserStory(
        project_id=project.id,
        code=next_code(project),
        external_id=external_id,
        status="borrador",
    )
    fields = {k: v for k, v in data.items() if k in EDITABLE_FIELDS or k == "assignee_id"}
    if epic_title:
        fields["epic_id"] = get_or_create_epic(db, project, epic_title).id
    for key, value in fields.items():
        setattr(story, key, value)
    db.add(story)
    db.flush()
    audit.record(db, story, actor, "created", to_status="borrador", diff=fields)
    return story


def upsert_story(
    db: Session,
    project: Project,
    data: dict,
    actor: Actor,
    external_id: str | None = None,
) -> tuple[UserStory, bool]:
    """Crea o actualiza una HU por external_id (o por título si no lo trae). Devuelve (hu, creada)."""
    story = None
    if external_id:
        story = db.scalar(
            select(UserStory).where(UserStory.project_id == project.id, UserStory.external_id == external_id)
        )
    elif data.get("title"):
        story = db.scalar(
            select(UserStory).where(UserStory.project_id == project.id, UserStory.title == data["title"])
        )

    if story is None:
        return create_story(db, project, data, actor, external_id), True

    apply_changes(db, project, story, data, actor)
    return story, False


def transition(
    db: Session,
    story: UserStory,
    to_status: str,
    actor: Actor,
    user_id: int | None = None,
    cancel_refinements: bool = True,
) -> RefinementRequest | None:
    """Cambia estado validando workflow y permisos. Devuelve la solicitud de refinamiento creada, si hubo."""
    from_status = story.status
    check_transition(from_status, to_status, actor)

    story.status = to_status
    story.version += 1
    audit.record(db, story, actor, "status_changed", from_status, to_status)

    if from_status == "en_refinamiento" and cancel_refinements:
        for req in story.refinements:
            if req.status in ("pending", "in_progress"):
                req.status = "cancelled"

    created = None
    if to_status == "en_refinamiento":
        created = RefinementRequest(
            project_id=story.project_id,
            story=story,
            status="pending",
            input_snapshot=snapshot(story),
            requested_by_id=user_id,
        )
        db.add(created)
        db.flush()
    return created


def list_stories(
    db: Session,
    project: Project,
    status: str | None = None,
    type_: str | None = None,
    epic_id: int | None = None,
    assignee_id: int | None = None,
) -> list[UserStory]:
    stmt = select(UserStory).where(UserStory.project_id == project.id)
    if status:
        stmt = stmt.where(UserStory.status == status)
    if type_:
        stmt = stmt.where(UserStory.type == type_)
    if epic_id:
        stmt = stmt.where(UserStory.epic_id == epic_id)
    if assignee_id:
        stmt = stmt.where(UserStory.assignee_id == assignee_id)
    stmt = stmt.order_by(UserStory.priority_rank.is_(None), UserStory.priority_rank, UserStory.id)
    return list(db.scalars(stmt))
