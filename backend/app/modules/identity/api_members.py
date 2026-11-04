"""/api/v1/identity: staff members, roles and invitations."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import permissions as P
from . import repository, service
from .schemas import InvitationIn, InvitationOut, MemberOut, MemberUpdate, RolesIn

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


def _member(db: Session, ctx: Context, membership_id: uuid.UUID) -> MemberOut:
    row = next((m for m in repository.list_members(db, ctx) if m["membership_id"] == membership_id), None)
    assert row is not None
    return MemberOut(**row)


@router.get("/members", response_model=list[MemberOut], summary="List staff", responses=ERRORS)
def list_members(ctx: Context = Depends(require(P.MEMBERSHIP_READ))) -> list[MemberOut]:
    with transaction(ctx) as db:
        return [MemberOut(**m) for m in service.list_members(db, ctx)]


@router.patch("/members/{membership_id}", response_model=MemberOut, summary="Update title or status", responses=ERRORS)
def update_member(
    membership_id: uuid.UUID, body: MemberUpdate, ctx: Context = Depends(require(P.MEMBERSHIP_WRITE))
) -> MemberOut:
    with transaction(ctx) as db:
        service.update_member(db, ctx, membership_id, body)
        return _member(db, ctx, membership_id)


@router.put(
    "/members/{membership_id}/roles", response_model=MemberOut, summary="Replace a member's roles", responses=ERRORS
)
def set_roles(
    membership_id: uuid.UUID, body: RolesIn, ctx: Context = Depends(require(P.MEMBERSHIP_WRITE))
) -> MemberOut:
    with transaction(ctx) as db:
        service.set_roles(db, ctx, membership_id, set(body.roles))
        return _member(db, ctx, membership_id)


@router.post(
    "/invitations", response_model=InvitationOut, status_code=201, summary="Invite a staff member", responses=ERRORS
)
def invite(body: InvitationIn, ctx: Context = Depends(require(P.MEMBERSHIP_WRITE))) -> InvitationOut:
    with transaction(ctx) as db:
        return service.invite(db, ctx, body)


@router.post(
    "/invitations/{invitation_id}/revoke", status_code=204, summary="Revoke a pending invitation", responses=ERRORS
)
def revoke_invitation(invitation_id: uuid.UUID, ctx: Context = Depends(require(P.MEMBERSHIP_WRITE))) -> None:
    with transaction(ctx) as db:
        service.revoke_invitation(db, ctx, invitation_id)
