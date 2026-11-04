"""Optional public-message safety classification through the configured safety class."""

from __future__ import annotations

import uuid

from app.platform.db import transaction

from .. import structured
from ..agent.contexts import CtxFactory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..gateway.router import Router, effective_policy, load_router
from ..schemas import SafetyVerdict


def screen(
    ctx_factory: CtxFactory, text: str, trigger_key: str, *, router: Router | None = None
) -> SafetyVerdict | None:
    router = router if router is not None else load_router()
    if router is None:
        return None
    ctx = ctx_factory(uuid.uuid4())
    with transaction(ctx) as db:
        policy = effective_policy(db, ctx)
    if not router.allowed("safety", policy):
        return None
    _, verdict = structured.complete(
        ctx_factory, use_case="safety", prompt=load_prompt("safety"),
        facts=[ContextBlock(label="message", text=text, untrusted=True)], output_model=SafetyVerdict,
        trigger_type="job", trigger_key=f"safety:{trigger_key}", router=router,
    )  # fmt: skip
    return verdict
