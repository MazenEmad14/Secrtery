"""The caller's own log of what the assistant proposed and did on their behalf."""
from fastapi import APIRouter, Depends, Query, Request

from helpers.auth_utils import get_current_user
from helpers.services import services_of
from models.ActionLogModel import ActionLogModel
from services.actions import history

actions_router = APIRouter(prefix="/api/v1/actions", tags=["actions"])


@actions_router.get("/log")
async def action_log(request: Request, limit: int = Query(50, ge=1, le=200), current_user: dict = Depends(get_current_user)):
    """One row per action (newest first): what it was, whether and when the user approved it, how it ended.
    Message bodies and document text are never stored here, only who/what/when."""
    events = await ActionLogModel(services_of(request).mongo_db).recent_events(str(current_user["_id"]), limit=limit * 7)
    return {"actions": history(events, limit=limit)}
