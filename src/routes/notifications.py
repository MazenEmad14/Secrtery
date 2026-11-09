"""The caller's persistent notifications, and how they want to receive them."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from helpers.auth_utils import get_current_user
from helpers.config import get_settings
from helpers.services import services_of
from models.NotificationModel import NotificationModel
from models.PushSubscriptionModel import PushSubscriptionModel
from models.UserModel import UserModel
from services.notifier import is_allowed_push_endpoint, notify, push_configured

notifications_router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class PreferencesRequest(BaseModel):
    push: Optional[bool] = None
    email: Optional[bool] = None


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1000)
    keys: PushKeys


class PushEndpointRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1000)


@notifications_router.get("")
async def list_notifications(request: Request, unread_only: bool = False, limit: int = Query(50, ge=1, le=200),
                             current_user: dict = Depends(get_current_user)):
    """The caller's notifications, newest first."""
    model = NotificationModel(services_of(request).mongo_db)
    items = await model.list_for_user(str(current_user["_id"]), unread_only=unread_only, limit=limit)
    return {"notifications": [NotificationModel.public(item) for item in items]}


@notifications_router.post("/read-all")
async def mark_all_read(request: Request, current_user: dict = Depends(get_current_user)):
    model = NotificationModel(services_of(request).mongo_db)
    return {"marked": await model.mark_all_read(str(current_user["_id"]))}


# ── channels ────────────────────────────────────────────────────────────────

@notifications_router.get("/preferences")
async def get_preferences(request: Request, current_user: dict = Depends(get_current_user)):
    """The channels the user switched on, and what this server can offer. The web app is always on."""
    user_id = str(current_user["_id"])
    db = services_of(request).mongo_db
    prefs = await UserModel(db).get_notification_prefs(user_id)
    return {
        **prefs,
        "push_available": push_configured(),
        "email_available": bool(current_user.get("google_token")),
        "push_browsers": len(await PushSubscriptionModel(db).list_for_user(user_id)),
    }


@notifications_router.put("/preferences")
async def set_preferences(body: PreferencesRequest, request: Request, current_user: dict = Depends(get_current_user)):
    """Switch optional channels on or off. A channel the server cannot deliver is refused, not silently ignored."""
    changes = body.model_dump(exclude_none=True)
    if changes.get("push") and not push_configured():
        raise HTTPException(status_code=409, detail="Web Push is not set up on this server.")
    if changes.get("email") and not current_user.get("google_token"):
        raise HTTPException(status_code=409, detail="Connect your Google account first: emails are sent from your own Gmail.")
    user_id = str(current_user["_id"])
    prefs = await UserModel(services_of(request).mongo_db).set_notification_prefs(user_id, changes)
    return prefs


@notifications_router.get("/push/public-key")
async def push_public_key(current_user: dict = Depends(get_current_user)):
    """The server's VAPID public key, which the browser needs to subscribe. None when Web Push is not set up."""
    return {"public_key": get_settings().VAPID_PUBLIC_KEY if push_configured() else None}


@notifications_router.post("/push/subscription")
async def save_push_subscription(body: PushSubscriptionRequest, request: Request,
                                 current_user: dict = Depends(get_current_user)):
    """Remember this browser so Web Push notifications can reach it."""
    if not push_configured():
        raise HTTPException(status_code=409, detail="Web Push is not set up on this server.")
    if not is_allowed_push_endpoint(body.endpoint):
        raise HTTPException(status_code=400, detail="That is not a push service address this server accepts.")
    await PushSubscriptionModel(services_of(request).mongo_db).save(
        str(current_user["_id"]), body.endpoint, body.keys.p256dh, body.keys.auth)
    return {"subscribed": True}


@notifications_router.post("/push/unsubscribe")
async def remove_push_subscription(body: PushEndpointRequest, request: Request,
                                   current_user: dict = Depends(get_current_user)):
    removed = await PushSubscriptionModel(services_of(request).mongo_db).remove(str(current_user["_id"]), body.endpoint)
    return {"removed": removed}


@notifications_router.post("/test")
async def send_test_notification(request: Request, current_user: dict = Depends(get_current_user)):
    """Send the caller a test notification through all the channels they switched on."""
    stored = await notify(services_of(request).mongo_db, str(current_user["_id"]), kind="test",
                          title="✅ Test notification", body="Notifications are working.")
    return {"sent": stored is not None, "notification": NotificationModel.public(stored) if stored else None}


@notifications_router.post("/{notification_id}/read")
async def mark_read(notification_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """Mark one notification read. Someone else's notification is reported as not found."""
    model = NotificationModel(services_of(request).mongo_db)
    if not await model.mark_read(str(current_user["_id"]), notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"read": True}
