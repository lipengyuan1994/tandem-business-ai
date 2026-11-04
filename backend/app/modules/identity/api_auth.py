"""/api/v1/auth: login (redirect), callback, me, business switch, logout."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.platform import audit, clock, ratelimit
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import SET_TENANT, transaction
from app.platform.errors import Forbidden, error_responses
from app.platform.permissions import SESSION_COOKIE, hash_token, require_session

from . import login, oidc, repository, sessions
from .roles import POLICY_VERSION, permissions_for
from .schemas import MembershipBrief, MeOut, SwitchBusinessIn, UserBrief

router = APIRouter()
log = structlog.get_logger(__name__)
_PUBLIC = Context(request_id="auth", actor_kind="visitor", user_id=None, business_id=None)
LOGIN_COOKIE = "__Host-smb_oidc"


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().staff_session_hours * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/login", summary="Start login (redirects to Keycloak)", response_class=RedirectResponse, status_code=302)
def start_login(request: Request, return_to: str = "/today") -> RedirectResponse:
    with transaction(_PUBLIC) as db:
        ratelimit.hit(db, f"login:{_client_ip(request)}", limit=30, window_seconds=60)
        req = oidc.build_login_request()
        browser_token = sessions.new_token()
        # The state lookup requires a secret held only by the initiating browser, never in the callback URL.
        login.save_state(
            db,
            state=f"{req.state}:{browser_token}",
            nonce=req.nonce,
            code_verifier=req.code_verifier,
            return_to=return_to,
        )
    response = RedirectResponse(req.url, status_code=302)
    response.set_cookie(
        LOGIN_COOKIE,
        browser_token,
        max_age=int(login.STATE_TTL.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/callback", summary="OIDC callback from Keycloak", response_class=RedirectResponse, status_code=302)
def callback(request: Request, state: str = "", code: str = "", error: str | None = None) -> Response:
    try:
        if error or not state or not code:
            raise oidc.LoginFailed(f"provider error: {error or 'missing code/state'}")
        with transaction(_PUBLIC) as db:
            ratelimit.hit(db, f"login:{_client_ip(request)}", limit=30, window_seconds=60)
            browser_token = request.cookies.get(LOGIN_COOKIE)
            if not browser_token:
                raise oidc.LoginFailed("missing browser correlation")
            saved = login.take_state(db, f"{state}:{browser_token}")
        id_token = oidc.exchange_code(code, saved.code_verifier)
        claims = oidc.validate_id_token(id_token, saved.nonce)
        with transaction(_PUBLIC) as db:
            user, business_id = login.complete_login(db, claims)
            presented = request.cookies.get(SESSION_COOKIE)
            if presented:
                previous = repository.get_session_by_hash(db, hash_token(presented))
                if previous is not None:
                    repository.revoke_sessions(db, user_id=previous.user_id, session_id=previous.id, now=clock.now())
            _, token, _csrf = sessions.create_session(
                db, user.id, business_id, ip=_client_ip(request), user_agent=request.headers.get("user-agent")
            )
    except oidc.LoginFailed as exc:
        log.warning("login_failed", reason=exc.reason)
        return RedirectResponse("/login-failed", status_code=302)
    target = saved.return_to if business_id else "/select-business"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    _set_cookie(response, token)
    return response


def _me(ctx: Context, csrf_token: str | None = None) -> MeOut:
    assert ctx.user_id is not None
    with transaction(
        Context(request_id=ctx.request_id, actor_kind="user", user_id=ctx.user_id, business_id=ctx.business_id)
    ) as db:
        user = repository.get_user(db, ctx.user_id)
        assert user is not None
        memberships = repository.memberships_of_user(db, ctx.user_id)
        assert ctx.session_id is not None or csrf_token is not None
        csrf = csrf_token or sessions.csrf_for(ctx.session_id)  # type: ignore[arg-type]
        tz = next((m["timezone"] for m in memberships if m["business_id"] == ctx.business_id), None)
        return MeOut(
            user=UserBrief(id=user.id, display_name=user.display_name),
            email=user.email,
            memberships=[
                MembershipBrief(
                    business_id=m["business_id"], business_name=m["name"], business_slug=m["slug"], roles=m["roles"]
                )
                for m in memberships
            ],
            active_business_id=ctx.business_id,
            active_business_timezone=tz,
            permissions=sorted(ctx.permissions),
            csrf_token=csrf,
            policy_version=POLICY_VERSION,
        )


@router.get(
    "/me",
    response_model=MeOut,
    summary="Current user, businesses, permissions and CSRF token",
    responses=error_responses(401),
)
def me(ctx: Context = Depends(require_session())) -> MeOut:
    return _me(ctx)


@router.post(
    "/active-business",
    response_model=MeOut,
    summary="Switch the active business (rotates the session)",
    responses=error_responses(401, 403),
)
def switch_business(body: SwitchBusinessIn, ctx: Context = Depends(require_session())) -> JSONResponse:
    assert ctx.user_id is not None
    with transaction(
        Context(request_id=ctx.request_id, actor_kind="user", user_id=ctx.user_id, business_id=None)
    ) as db:
        db.execute(SET_TENANT, {"b": str(body.business_id), "u": str(ctx.user_id)})
        roles = repository.active_roles(db, ctx.user_id, body.business_id)
        if not roles:
            raise Forbidden("identity.membership_inactive")
        token, csrf = sessions.rotate(db, ctx, body.business_id)
        new_ctx = Context(
            request_id=ctx.request_id,
            actor_kind="user",
            user_id=ctx.user_id,
            business_id=body.business_id,
            permissions=permissions_for(roles),
        )
        audit.record(
            db,
            new_ctx,
            action="identity.session.business_switched",
            target_type="business",
            target_id=body.business_id,
            summary="Switched active business",
        )
    response = JSONResponse(_me(new_ctx, csrf_token=csrf).model_dump(mode="json"))
    _set_cookie(response, token)
    return response


@router.post(
    "/logout", summary="Log out this session; returns the Keycloak end-session URL", responses=error_responses(401)
)
def logout(ctx: Context = Depends(require_session())) -> JSONResponse:
    assert ctx.user_id is not None
    with transaction(
        Context(request_id=ctx.request_id, actor_kind="user", user_id=ctx.user_id, business_id=None)
    ) as db:
        repository.revoke_sessions(db, user_id=ctx.user_id, session_id=ctx.session_id, now=clock.now())
    response = JSONResponse({"logout_url": oidc.end_session_url()})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/logout-all", summary="Revoke every session of the current user", responses=error_responses(401))
def logout_all(ctx: Context = Depends(require_session())) -> JSONResponse:
    assert ctx.user_id is not None
    with transaction(
        Context(request_id=ctx.request_id, actor_kind="user", user_id=ctx.user_id, business_id=None)
    ) as db:
        count = repository.revoke_sessions(db, user_id=ctx.user_id, session_id=None, now=clock.now())
    response = JSONResponse({"revoked": count, "logout_url": oidc.end_session_url()})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
