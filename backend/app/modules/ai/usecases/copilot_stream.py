"""Worker-to-SSE bridge, with cancellation and generation-slot cleanup."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from ..agent.protocol import StepEvent

if TYPE_CHECKING:
    from .copilot import PreparedMessage


async def stream_message(prepared: PreparedMessage) -> AsyncGenerator[str]:
    """A cancelled/disconnected response stops the worker at its next boundary."""
    from .copilot import release_generation, run_message

    if prepared is not None and prepared.replay is not None:
        replay = prepared.replay
        yield f"event: {replay.kind}\ndata: {replay.model_dump_json()}\n\n"
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[StepEvent | None] = asyncio.Queue()
    stopped = threading.Event()
    started = threading.Event()
    ownership = threading.Lock()

    def put(event: StepEvent | None) -> None:
        if not stopped.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        with ownership:
            if stopped.is_set():
                return
            started.set()
        try:
            run_message(prepared, put, stopped)
        finally:
            if prepared is not None:
                release_generation(prepared)
            put(None)

    future = loop.run_in_executor(None, worker)
    try:
        while (event := await queue.get()) is not None:
            yield f"event: {event.kind}\ndata: {event.model_dump_json()}\n\n"
            if event.kind in ("final", "error"):
                break
        # Propagate only failures that happened before the worker could create a run/error event.
        if future.done():
            future.result()
    finally:
        with ownership:
            stopped.set()
            future.cancel()
            if not started.is_set() and prepared is not None:
                release_generation(prepared)
