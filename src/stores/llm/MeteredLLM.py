"""Wraps a model client so every call is counted against the user it is for, and is refused once that user has used
the day's allowance (services/usage.py). Wrapping the client, instead of touching each caller, means no path to the
model (a chat node, a background job, the inbox check, document indexing) can bypass the meter or the ceiling.

The user comes from the request context (`user_id_var`); background work sets it with `as_user`.

Async methods are the normal case. The sync ones exist because some controllers run in worker threads: they hand the
database work to the application's event loop and wait for it. Called from the loop's own thread they cannot wait, so
they count the call but skip the ceiling check (nothing waits on the loop from there today).
"""
import asyncio
import logging
from typing import Optional

from services.usage import UsageMeter, current_user, estimate_tokens

logger = logging.getLogger("uvicorn.error")

SYNC_WAIT_SECONDS = 10


class MeteredLLM:
    def __init__(self, inner, meter: UsageMeter, loop: Optional[asyncio.AbstractEventLoop] = None):
        self._inner = inner
        self._meter = meter
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def __getattr__(self, name):   # model ids, enums, embedding_size, construct_prompt ... all come from the provider
        return getattr(self._inner, name)

    # ── bridging sync code to the meter ──────────────────────────────────────
    def _call_from_thread(self, coro, wait: bool):
        """Run a meter coroutine on the application's loop from a worker thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            coro.close()
            return
        try:
            asyncio.get_running_loop()
            on_loop_thread = True
        except RuntimeError:
            on_loop_thread = False
        if on_loop_thread:
            loop.create_task(coro)       # cannot wait here; record it, skip the check
            return
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        if wait:
            future.result(timeout=SYNC_WAIT_SECONDS)

    # ── generation ───────────────────────────────────────────────────────────
    async def agenerate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                             temperature: float = None, json_mode: bool = False):
        user = current_user()
        await self._meter.check_allowed(user)
        extra = {"json_mode": True} if json_mode else {}
        try:
            reply = await self._inner.agenerate_text(prompt, chat_history, max_output_tokens, temperature, **extra)
        except Exception:
            # A failed call can still have cost something, and a retry storm must not run for free.
            await self._meter.record_generation(user, estimate_tokens(prompt, chat_history), 0)
            raise
        await self._meter.record_generation(user, estimate_tokens(prompt, chat_history), estimate_tokens(reply))
        return reply

    def generate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                      temperature: float = None, json_mode: bool = False):
        user = current_user()
        self._call_from_thread(self._meter.check_allowed(user), wait=True)
        extra = {"json_mode": True} if json_mode else {}
        try:
            reply = self._inner.generate_text(prompt, chat_history, max_output_tokens, temperature, **extra)
        except Exception:
            self._call_from_thread(self._meter.record_generation(user, estimate_tokens(prompt, chat_history), 0), wait=False)
            raise
        self._call_from_thread(
            self._meter.record_generation(user, estimate_tokens(prompt, chat_history), estimate_tokens(reply)), wait=False)
        return reply

    # ── embeddings ───────────────────────────────────────────────────────────
    async def aembed_text(self, text: str, document_type: str = None):
        user = current_user()
        await self._meter.check_allowed(user)
        vector = await self._inner.aembed_text(text, document_type)
        await self._meter.record_embedding(user, estimate_tokens(text))
        return vector

    async def aembed_texts(self, texts: list[str], document_type: str = None):
        user = current_user()
        await self._meter.check_allowed(user)
        vectors = await self._inner.aembed_texts(texts, document_type)
        await self._meter.record_embedding(user, estimate_tokens(*texts))
        return vectors

    def embed_text(self, text: str, document_type: str = None):
        user = current_user()
        self._call_from_thread(self._meter.check_allowed(user), wait=True)
        vector = self._inner.embed_text(text, document_type)
        self._call_from_thread(self._meter.record_embedding(user, estimate_tokens(text)), wait=False)
        return vector

    def embed_texts(self, texts: list[str], document_type: str = None):
        user = current_user()
        self._call_from_thread(self._meter.check_allowed(user), wait=True)
        vectors = self._inner.embed_texts(texts, document_type)
        self._call_from_thread(self._meter.record_embedding(user, estimate_tokens(*texts)), wait=False)
        return vectors
