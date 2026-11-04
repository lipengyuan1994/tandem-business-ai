"""/api/v1/admin: the System page, data export and customer erasure."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.platform import ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import permissions as P
from . import service
from .models import ErasureRequest
from .schemas import ErasureIn, ErasureOut, SystemStatus, TestEmailIn, TestEmailOut

router = APIRouter()


@router.get(
    "/system",
    response_model=SystemStatus,
    summary="Version, queues, delivery issues and backups",
    responses=error_responses(401, 403),
)
def system(ctx: Context = Depends(require(P.SYSTEM_READ))) -> SystemStatus:
    with transaction(ctx) as db:
        return service.system_status(db, ctx)


@router.post(
    "/exports",
    summary="Download all of this business's data as a ZIP",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}, **error_responses(401, 403, 429)},
)
def export_data(ctx: Context = Depends(require(P.DATA_MANAGE))) -> Response:
    with transaction(ctx) as db:
        ratelimit.hit(db, f"export:{ctx.business_id}", limit=5, window_seconds=3600)
        data, manifest = service.build_export(db, ctx)
    stamp = manifest["exported_at"][:10]
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="export-{stamp}.zip"', "Cache-Control": "no-store"},
    )


def _out(r: ErasureRequest) -> ErasureOut:
    return ErasureOut(id=r.id, customer_id=r.customer_id, status=r.status, report=r.report, completed_at=r.completed_at)


@router.post(
    "/erasures",
    response_model=ErasureOut,
    summary="Erase one customer's personal data",
    responses=error_responses(401, 403, 404, 409, 422),
)
def erase(body: ErasureIn, ctx: Context = Depends(require(P.DATA_MANAGE))) -> ErasureOut:
    with transaction(ctx) as db:
        return _out(service.erase_customer(db, ctx, body.customer_id, body.confirm_name))


@router.get(
    "/erasures", response_model=list[ErasureOut], summary="Erasures carried out", responses=error_responses(401, 403)
)
def erasures(ctx: Context = Depends(require(P.DATA_MANAGE))) -> list[ErasureOut]:
    with transaction(ctx) as db:
        return [_out(r) for r in service.list_erasures(db, ctx)]


@router.post(
    "/test-email",
    response_model=TestEmailOut,
    status_code=202,
    summary="Queue a test e-mail",
    responses=error_responses(401, 403, 422, 429),
)
def test_email(body: TestEmailIn, ctx: Context = Depends(require(P.SYSTEM_READ))) -> TestEmailOut:
    with transaction(ctx) as db:
        ratelimit.hit(db, f"test-email:{ctx.business_id}", limit=5, window_seconds=3600)
        return TestEmailOut(outbox_id=service.enqueue_test_email(db, ctx, str(body.to)))
