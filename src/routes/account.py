"""The user's rights over their own data: download everything, or delete the account."""
import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from helpers import auth_utils
from helpers.rate_limits import per_user_limit
from helpers.services import services_of
from helpers.session_cookies import clear_auth_cookies
from services import account as account_service

logger = logging.getLogger("uvicorn.error")

account_router = APIRouter(prefix="/api/v1/account", tags=["account"])

export_limit = per_user_limit("export", "ACCOUNT_EXPORT_MAX_PER_HOUR", "data exports", window_seconds=3600)
delete_limit = per_user_limit("account-delete", "ACCOUNT_DELETE_MAX_PER_HOUR", "account deletion attempts", window_seconds=3600)


class DeleteAccountRequest(BaseModel):
    # An account with a password proves it with the password; a Google-only account types its email address.
    password: Optional[str] = Field(default=None, max_length=200)
    confirm_email: Optional[str] = Field(default=None, max_length=320)


@account_router.get("/export", dependencies=[Depends(export_limit)])
async def export_my_data(request: Request, current_user: dict = Depends(auth_utils.get_current_user)):
    """A ZIP with everything the app keeps about the caller: export.json and the documents they uploaded."""
    path = await account_service.build_export(services_of(request), current_user)
    filename = f"nexusai-export-{datetime.utcnow():%Y-%m-%d}.zip"
    cleanup = BackgroundTasks()
    cleanup.add_task(os.remove, path)               # the temporary file goes once the download has been sent
    return FileResponse(path, media_type="application/zip", filename=filename, background=cleanup)


@account_router.post("/delete", dependencies=[Depends(delete_limit)])
async def delete_my_account(body: DeleteAccountRequest, request: Request, response: Response,
                            current_user: dict = Depends(auth_utils.get_current_user)):
    """Permanently delete the caller's account and all their data. This cannot be undone.

    The caller must prove it is them again (password, or the email address for Google-only accounts), so a session
    left open on a shared computer cannot erase an account."""
    stored_hash = current_user.get("password_hash")
    if stored_hash:
        if not body.password or not await asyncio.to_thread(auth_utils.verify_password, body.password, stored_hash):
            raise HTTPException(status_code=403, detail="That password is not right, so nothing was deleted.")
    elif (body.confirm_email or "").strip().lower() != current_user.get("email", "").lower():
        raise HTTPException(status_code=403, detail="Type your account's email address to confirm. Nothing was deleted.")

    summary = await account_service.delete_account(services_of(request), current_user)
    clear_auth_cookies(response)
    return summary
