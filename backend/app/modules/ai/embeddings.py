"""Embedding facade: metered non-chat calls, without retaining input text or vectors in AI records."""

import uuid
from dataclasses import replace

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.registry import EmbeddingBatch

from . import recording, usage
from .agent.prompts import Prompt
from .chains import run_context
from .errors import AiError
from .gateway.router import effective_policy, load_router
from .gateway.types import EmbedRequest
from .model_calls import budget_scope, policy_scope
from .schemas import Policy


def embedding_model(db_ctx: Context) -> str | None:
    router = load_router()
    if router is None:
        return None
    with transaction(db_ctx) as db:
        if usage.effective_budget(db, db_ctx) <= 0:
            return None
        entries = router.allowed("embed", effective_policy(db, db_ctx))
    return f"{entries[0].provider}/{entries[0].model}" if entries else None


def _policy(ctx: Context) -> Policy:
    with transaction(ctx) as db:
        return effective_policy(db, ctx)


def embed_texts(ctx: Context, texts: list[str], *, query: bool = False) -> EmbeddingBatch:
    router = load_router()
    if router is None:
        raise AiError("ai.not_configured")
    own_run = ctx.ai_run_id is None
    run_ctx = (
        run_context(replace(ctx, ai_run_id=uuid.uuid4()), "knowledge_query" if query else "knowledge_index")
        if own_run
        else ctx
    )
    if own_run:
        recording.open_run(
            run_ctx,
            recording.RunMeta(
                use_case="knowledge_index" if not query else "knowledge_query",
                prompt=Prompt(name="embedding", version=1, model_class="embed", output="vector(1024)", text=""),
                trigger_type="job" if not query else "api",
                trigger_key=f"embed:{uuid.uuid4()}",
            ),
        )
    try:
        # Public front desk retrieval shares its parent's public token allowance and chain admission.
        req = EmbedRequest(
            inputs=texts,
            use_case="intake"
            if query and ctx.agent_key == "front_desk"
            else "knowledge_query"
            if query
            else "knowledge_index",
            timeout_s=min(10.0 if query else 30.0, float(router.limits.step_timeout_s)),
        )
        with budget_scope(run_ctx), policy_scope(lambda: _policy(ctx)):
            result, _ = router.embed(_policy(ctx), req)
        if own_run:
            assert run_ctx.ai_run_id is not None
            recording.close_run(run_ctx, run_ctx.ai_run_id, "succeeded", result={"inputs": len(texts)})
        return EmbeddingBatch(result.embeddings, f"{result.provider}/{result.model}")
    except Exception:
        if own_run:
            assert run_ctx.ai_run_id is not None
            recording.close_run(run_ctx, run_ctx.ai_run_id, "failed", error_code="ai.embedding_failed")
        raise


class EmbeddingGateway:
    model = staticmethod(embedding_model)
    embed = staticmethod(embed_texts)
