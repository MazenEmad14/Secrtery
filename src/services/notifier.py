"""One path for every notification: store it, then offer it to the channels the user switched on.

The stored notification (collection `notifications`) *is* the web channel: the app's stream and list read it, so
it survives a restart and waits for a user who is offline. Web Push and email-to-self are optional extra channels,
chosen per user in Settings. A failing optional channel never affects the stored notification; its outcome is
recorded on the notification (`delivery`), never silently dropped.

Optional channels are tried once, right after the notification is first stored. They are not retried yet: a
retry needs the idempotency keys and action log of PR 0.4, otherwise a crash could send the same email twice.
"""
import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urlparse

from helpers.config import Settings, get_settings
from helpers.privacy import mask_email
from models.NotificationModel import NotificationModel
from models.PushSubscriptionModel import PushSubscriptionModel
from models.UserModel import UserModel

logger = logging.getLogger("uvicorn.error")

# Push services of the major browsers. The browser tells us where to POST, so an unchecked endpoint would let a user
# make the server call any address they like: only these hosts are accepted.
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",            # Chrome, Edge, Android
    "push.services.mozilla.com",     # Firefox
    "push.apple.com",                # Safari
    "notify.windows.com",            # Windows notification service (Edge)
)
MAX_PUSH_TEXT = 200


def is_allowed_push_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and parsed.port in (None, 443) and len(endpoint) <= 1000
            and any(host == suffix or host.endswith("." + suffix) for suffix in PUSH_HOST_SUFFIXES))


def push_configured(settings: Optional[Settings] = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


class PushGone(Exception):
    """The push service says this subscription no longer exists (the user revoked it or cleared the browser)."""


def send_web_push(subscription: dict, payload: dict, settings: Settings) -> None:
    """Blocking: send one Web Push message. Raises PushGone for an expired subscription, other errors as they are."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": subscription["endpoint"], "keys": subscription["keys"]},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},  # a fresh dict: the library adds fields to it
            ttl=24 * 3600,
            timeout=10,
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            raise PushGone() from exc
        raise


def send_gmail_to_self(token: dict, to: str, subject: str, body: str):
    """Blocking: send a plain email from the user's own Gmail to themself. Returns the refreshed token, if any."""
    from stores.gmail.GmailService import GmailService

    gmail = GmailService()
    refreshed = gmail.authenticate_from_dict(token)
    gmail.send_email(to=to, subject=subject, body=body)
    return refreshed


async def _deliver_push(db, user_id: str, notification: dict, settings: Settings) -> dict:
    if not push_configured(settings):
        return {"status": "skipped", "reason": "Web Push is not configured on this server"}
    subscriptions = PushSubscriptionModel(db)
    targets = await subscriptions.list_for_user(user_id)
    if not targets:
        return {"status": "skipped", "reason": "no browser has Web Push switched on"}
    payload = {"title": notification["title"][:MAX_PUSH_TEXT], "body": (notification.get("body") or "")[:MAX_PUSH_TEXT],
               "id": str(notification["_id"])}
    sent = failed = 0
    for target in targets:
        if not is_allowed_push_endpoint(target["endpoint"]):
            await subscriptions.remove_expired(target["endpoint"])
            continue
        try:
            await asyncio.to_thread(send_web_push, target, payload, settings)
            sent += 1
        except PushGone:
            await subscriptions.remove_expired(target["endpoint"])
        except Exception:
            failed += 1
            logger.warning("Web Push delivery failed for one browser", exc_info=True)
    return {"status": "sent" if sent else "failed" if failed else "skipped", "sent": sent, "failed": failed}


async def _deliver_email(db, user: dict, notification: dict) -> dict:
    users = UserModel(db)
    token = await users.get_google_token(user["email"])
    if not token:
        return {"status": "skipped", "reason": "Google is not connected"}
    body = (notification.get("body") or "").strip() or notification["title"]
    refreshed = await asyncio.to_thread(send_gmail_to_self, token, user["email"], notification["title"], body)
    if refreshed and refreshed != token:
        await users.update_google_token(user["email"], refreshed)
    return {"status": "sent"}


async def _dispatch(db, user_id: str, notification: dict, settings: Settings) -> None:
    """Offer a freshly stored notification to the user's optional channels and record what happened."""
    users = UserModel(db)
    user = await users.get_user_by_id(user_id)
    if user is None:
        return
    prefs = await users.get_notification_prefs(user_id)
    delivery: dict = {}
    if prefs["push"]:
        try:
            delivery["push"] = await _deliver_push(db, user_id, notification, settings)
        except Exception as exc:
            logger.error("Web Push channel failed", exc_info=True)
            delivery["push"] = {"status": "failed", "error": type(exc).__name__}
    if prefs["email"]:
        try:
            delivery["email"] = await _deliver_email(db, user, notification)
        except Exception as exc:
            logger.error(f"Email-to-self channel failed for {mask_email(user['email'])}", exc_info=True)
            delivery["email"] = {"status": "failed", "error": type(exc).__name__}
    if delivery:
        await NotificationModel(db).record_delivery(notification["_id"], delivery)


async def notify(db, user_id: str, kind: str, title: str, body: str = "", data: Optional[dict] = None,
                 dedupe_key: Optional[str] = None, settings: Optional[Settings] = None) -> Optional[dict]:
    """Store a notification for the user and offer it to their optional channels.

    With a `dedupe_key`, the same notification is stored (and sent) once, however often this is called.
    Returns the stored notification, or None when it already existed.
    """
    notification, created = await NotificationModel(db).create(
        user_id=user_id, kind=kind, title=title, body=body, data=data, dedupe_key=dedupe_key)
    if not created:
        return None
    try:
        await _dispatch(db, user_id, notification, settings or get_settings())
    except Exception:
        logger.error("Could not dispatch a notification to the optional channels", exc_info=True)
    return notification
