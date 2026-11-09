"""The caller's persistent notifications (reminders that came due, and later briefs and alerts)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from helpers.auth_utils import get_current_user
from helpers.services import services_of
from models.NotificationModel import NotificationModel

notifications_router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


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


@notifications_router.post("/{notification_id}/read")
async def mark_read(notification_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """Mark one notification read. Someone else's notification is reported as not found."""
    model = NotificationModel(services_of(request).mongo_db)
    if not await model.mark_read(str(current_user["_id"]), notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"read": True}
