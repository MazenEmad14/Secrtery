"""Email the app sends to users who have no Gmail linked (today only the address-verification link).

Delivery is "log" for now: the link is written to the server log, which is enough for development and a demo and
nothing else. Before real users, pick a transactional email provider (the business model document says so) and add
it here; REQUIRE_EMAIL_VERIFICATION stays refused until then. The log line is the only place the link appears, so
never run EMAIL_DELIVERY=log where the log is readable by people who should not be able to verify other people.
"""
import logging

from helpers.config import Settings
from helpers.privacy import mask_email

logger = logging.getLogger("uvicorn.error")


def verification_link(settings: Settings, raw_token: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/verify-email?token={raw_token}"


def send_verification_email(settings: Settings, email: str, raw_token: str) -> None:
    link = verification_link(settings, raw_token)
    if settings.EMAIL_DELIVERY == "log":
        logger.info(f"[development] Email verification link for {mask_email(email)}: {link}")
        return
    raise RuntimeError(f"unknown EMAIL_DELIVERY {settings.EMAIL_DELIVERY!r}")
