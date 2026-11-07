"""Team controls use the existing Activity/read and autonomy/write permissions."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import team_activity, team_service
from .permissions import ASSISTANT_USE
from .team_schemas import AddressableAgentsOut, AgentOut, AgentPatch, AgentsOut, ChainOut, TeamActivityPage
from .team_settings import patch_settings

router = APIRouter()


@router.get("/agents", response_model=AgentsOut, responses=error_responses(401, 403))
def agents(ctx: Context = Depends(require("approvals.proposal.read"))) -> AgentsOut:
    with transaction(ctx) as db:
        return team_service.list_agents(db, ctx)


@router.get("/agents/addressable", response_model=AddressableAgentsOut, responses=error_responses(401, 403))
def addressable(ctx: Context = Depends(require(ASSISTANT_USE))) -> AddressableAgentsOut:
    with transaction(ctx) as db:
        return team_service.addressable_agents(db, ctx)


@router.patch("/agents/{key}", response_model=AgentOut, responses=error_responses(401, 403, 404, 409, 422))
def patch_agent(key: str, body: AgentPatch, ctx: Context = Depends(require("org.settings.write"))) -> AgentOut:
    with transaction(ctx) as db:
        patch_settings(db, ctx, key, body)
        return team_service.agent_out(db, ctx, key)


@router.get(
    "/agents/{key}/activity", response_model=TeamActivityPage, responses=error_responses(400, 401, 403, 404, 422)
)
def agent_activity(
    key: str,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: Context = Depends(require("approvals.proposal.read")),
) -> TeamActivityPage:
    with transaction(ctx) as db:
        return team_activity.activity(db, ctx, key, cursor=cursor, limit=limit)


@router.get("/chains/{correlation_id}", response_model=ChainOut, responses=error_responses(401, 403, 404))
def chain(correlation_id: UUID, ctx: Context = Depends(require("approvals.proposal.read"))) -> ChainOut:
    with transaction(ctx) as db:
        return team_activity.chain(db, ctx, correlation_id)
