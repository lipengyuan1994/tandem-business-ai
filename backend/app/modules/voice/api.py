from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from starlette.concurrency import run_in_threadpool

from app.modules.ai import public as ai
from app.platform.context import Context
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import service
from .upload import read_audio

router = APIRouter()
MULTIPART = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["audio"],
                    "properties": {
                        "audio": {"type": "string", "format": "binary", "description": "At most 4 MiB / 120 seconds"},
                        "context": {
                            "type": "string",
                            "enum": [
                                "assistant",
                                "inbox_reply",
                                "purchasing",
                                "inventory",
                                "knowledge",
                                "note",
                                "other",
                            ],
                            "default": "other",
                        },
                        "entity_ids": {"type": "array", "maxItems": 10, "items": {"type": "string", "format": "uuid"}},
                    },
                }
            }
        },
    }
}


@router.post(
    "/transcriptions",
    response_model=ai.DictationOut,
    openapi_extra=MULTIPART,
    summary="Dictate editable text; never creates or sends a message",
    responses={
        **error_responses(401, 403, 409, 422, 429),
        413: {"description": "Audio too large"},
        503: {"description": "Provider unavailable"},
    },
)
async def transcriptions(
    request: Request,
    ctx: Context = Depends(require("ai.assistant.use")),
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=64)] = None,
) -> ai.DictationOut:
    upload = await read_audio(request)
    return await run_in_threadpool(service.transcribe, ctx, upload, idempotency_key)
