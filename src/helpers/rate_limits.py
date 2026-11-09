"""Per-user safety rate limits for the routes that cost money or trigger actions.

A fixed window per user and bucket, counted in MongoDB (models/RateLimitModel.py), so the limit holds across workers
and restarts. Going over answers 429 with a Retry-After header and a sentence saying how long to wait. Each allowed
request is also counted in the user's daily usage (shadow metering).
"""
import math
from datetime import datetime

from fastapi import Depends, Request

from helpers.auth_utils import get_current_user
from helpers.config import get_settings
from helpers.exceptions import RateLimitExceeded
from helpers.services import services_of
from models.RateLimitModel import RateLimitModel

WINDOW_SECONDS = 60


def per_user_limit(bucket: str, setting_name: str, what: str, count_as: str = "", window_seconds: int = WINDOW_SECONDS):
    """A route dependency: at most `setting_name` requests per window (a minute unless told otherwise) per user in this bucket."""

    async def dependency(request: Request, current_user: dict = Depends(get_current_user)) -> None:
        services = services_of(request)
        user_id = str(current_user["_id"])
        limits = RateLimitModel(services.mongo_db)
        key = f"{bucket}:{user_id}"
        if await limits.hit(key, window_seconds) > getattr(get_settings(), setting_name):
            _, ends = await limits.status(key)
            wait = math.ceil((ends - datetime.utcnow()).total_seconds()) if ends else window_seconds
            raise RateLimitExceeded(what, retry_after=wait)
        meter = getattr(services, "usage", None)
        if meter is not None and count_as:
            await meter.record_request(user_id, count_as)

    return dependency


chat_limit = per_user_limit("chat", "CHAT_MAX_PER_MINUTE", "messages", count_as="chat")
action_limit = per_user_limit("action", "ACTION_MAX_PER_MINUTE", "approvals", count_as="action")
voice_limit = per_user_limit("voice", "VOICE_MAX_PER_MINUTE", "voice requests")
