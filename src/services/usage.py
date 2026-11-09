"""Usage metering (shadow) and the daily cost ceiling.

Shadow mode (USAGE_METERING=shadow): record what each user uses, every day, so we learn what a user really costs; the
only limits enforced are safety limits (rate limits, and a per-user daily cost ceiling against a runaway loop). There
are no plan limits yet.

Costs are estimates. Tokens are estimated from text length (deliberately high: about 3 characters per token, and
Arabic and code tokenise worse than English), and priced with the assumptions in the settings. Hidden reasoning
tokens are not seen, so real usage can be higher. Replace the assumptions with real invoices when there are some.
"""
import logging
import math
from contextlib import contextmanager

from helpers.config import Settings
from helpers.exceptions import UsageLimitReached
from helpers.logging_setup import user_id_var
from models.UsageModel import UsageModel

logger = logging.getLogger("uvicorn.error")

CHARS_PER_TOKEN = 3
SYSTEM_USER = "system"   # model calls made with no user behind them (none today) are recorded here


def _characters(value) -> int:
    if isinstance(value, dict):      # a chat message
        return _characters(value.get("content", ""))
    if isinstance(value, (list, tuple)):
        return sum(_characters(item) for item in value)
    return len(str(value or ""))


def estimate_tokens(*texts) -> int:
    """A deliberately generous token estimate for texts, chat messages or a chat history."""
    return math.ceil(sum(_characters(text) for text in texts) / CHARS_PER_TOKEN)


@contextmanager
def as_user(user_id: str):
    """Background work (a job, the inbox check, the daily summary) runs on behalf of a user: say which, so its model
    calls are counted against them and stopped by their ceiling."""
    token = user_id_var.set(str(user_id))
    try:
        yield
    finally:
        user_id_var.reset(token)


def current_user() -> str:
    return user_id_var.get() or ""


class UsageMeter:
    def __init__(self, db, settings: Settings):
        self.usage = UsageModel(db)
        self.settings = settings

    # ── the ceiling ──────────────────────────────────────────────────────────
    async def check_allowed(self, user_id: str) -> None:
        """Raise UsageLimitReached when the user has used today's allowance. A database problem lets the call
        through (and is logged): the ceiling is a safety net, it must not be the reason chat stops."""
        if not user_id or user_id == SYSTEM_USER:
            return
        try:
            spent = await self.usage.cost_today(user_id)
        except Exception:
            logger.error("Could not check the daily usage ceiling", exc_info=True)
            return
        if spent >= self.settings.USER_DAILY_COST_CEILING_USD:
            logger.warning(f"User {user_id} reached the daily cost ceiling (about ${spent:.2f})")
            raise UsageLimitReached()

    # ── recording ────────────────────────────────────────────────────────────
    async def _add(self, user_id: str, increments: dict) -> None:
        try:
            await self.usage.add(user_id or SYSTEM_USER, increments)
        except Exception:
            logger.error("Could not record usage", exc_info=True)   # metering must never break the request

    async def record_generation(self, user_id: str, input_tokens: int, output_tokens: int) -> None:
        s = self.settings
        cost = (input_tokens * s.LLM_COST_PER_1K_INPUT_TOKENS_USD + output_tokens * s.LLM_COST_PER_1K_OUTPUT_TOKENS_USD) / 1000
        await self._add(user_id, {"llm_calls": 1, "input_tokens": input_tokens, "output_tokens": output_tokens,
                                  "est_cost_usd": cost})

    async def record_embedding(self, user_id: str, tokens: int) -> None:
        cost = tokens * self.settings.EMBEDDING_COST_PER_1K_TOKENS_USD / 1000
        await self._add(user_id, {"embedding_calls": 1, "embedding_tokens": tokens, "est_cost_usd": cost})

    async def record_transcription(self, user_id: str, seconds: float) -> None:
        cost = seconds / 60 * self.settings.TRANSCRIPTION_COST_PER_MINUTE_USD
        await self._add(user_id, {"transcription_seconds": round(seconds, 2), "est_cost_usd": cost})

    async def record_whatsapp_message(self, user_id: str, count: int = 1) -> None:
        """Called by the WhatsApp channel (Wave 4) for every message sent or received for a user."""
        await self._add(user_id, {"whatsapp_messages": count})

    async def record_request(self, user_id: str, kind: str) -> None:
        await self._add(user_id, {"chat_requests" if kind == "chat" else "action_requests": 1})

    # ── reading ──────────────────────────────────────────────────────────────
    async def summary(self, user_id: str, days: int = 7) -> dict:
        today = await self.usage.get_day(user_id)
        ceiling = self.settings.USER_DAILY_COST_CEILING_USD
        return {
            "metering": self.settings.USAGE_METERING,
            "today": today,
            "daily_ceiling_usd": ceiling,
            "ceiling_used_fraction": round(min(today["est_cost_usd"] / ceiling, 1.0), 4),
            "paused": today["est_cost_usd"] >= ceiling,
            "days": await self.usage.list_days(user_id, days),
            "note": "Costs are estimates (tokens are estimated from text length).",
        }

