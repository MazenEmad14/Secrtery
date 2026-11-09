"""The caller's own usage: what the assistant used for them today and in the last days, and how close they are to
the daily safety ceiling. Costs are estimates (see services/usage.py)."""
from fastapi import APIRouter, Depends, Query, Request

from helpers.auth_utils import get_current_user
from helpers.services import services_of

usage_router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


@usage_router.get("")
async def my_usage(request: Request, days: int = Query(7, ge=1, le=60), current_user: dict = Depends(get_current_user)):
    return await services_of(request).usage.summary(str(current_user["_id"]), days)
