"""How an action node runs through the audit log: see services/actions.py.

A node describes the outside call as `run()`; this module checks the approval, runs it at most once, and turns the
outcome into the reply, so the four actions (email, event, reminder, Drive file) behave the same way.
"""
import logging
from typing import Awaitable, Callable, Optional

from agent.deps import AgentDeps
from agent.errors import fail
from agent.google_clients import GOOGLE_CONNECT_MESSAGE
from agent.state import AgentState
from controllers.formatter import ResponseFormatter
from helpers.exceptions import GoogleAuthRequiredException
from models.ActionLogModel import ActionLogModel
from services.actions import ActionNotApproved, ActionOutcome, run_once

logger = logging.getLogger("uvicorn.error")

NOT_APPROVED = "I can't do that: there is no approval on record for this exact action, so nothing was done."
UNCONFIRMED = {
    "send_email": "I couldn't confirm whether the email was sent. Please check your Sent folder before trying again, "
                  "so it isn't sent twice.",
    "drive_save": "I couldn't confirm whether the file was saved to Drive. Please check your Drive before trying again, "
                  "so it isn't saved twice.",
}
IN_PROGRESS = "This action is already being carried out. Please wait a moment and check again."


def _log_for(deps: AgentDeps):
    if deps.action_log is not None:
        return deps.action_log
    return ActionLogModel(deps.db_client) if deps.db_client is not None else None


async def execute_action(state: AgentState, deps: AgentDeps, action_type: str, run: Callable[[], Awaitable[dict]],
                         idempotent: bool = False, result_id_key: str = "") -> Optional[ActionOutcome]:
    """Run an approved action once. Returns the outcome, or None after marking the state as refused (no approval)."""
    log = _log_for(deps)
    action_id = (state.get("metadata") or {}).get("action_id")
    if log is None or not action_id:
        logger.error(f"Refusing to run {action_type}: no approval record to check")
        _refuse(state)
        return None
    try:
        return await run_once(log, state.get("user_id", ""), action_id, action_type, state.get("action_details", {}),
                              run, idempotent=idempotent, result_id_key=result_id_key)
    except ActionNotApproved as exc:
        logger.error(f"Refusing to run {action_type}: {exc}")
        _refuse(state)
        return None


def _refuse(state: AgentState) -> None:
    state["result"] = ResponseFormatter.format_error(NOT_APPROVED)
    state["status"] = "error"
    state["metadata"]["error_code"] = "not_approved"


def report(state: AgentState, action_type: str, outcome: Optional[ActionOutcome],
           on_success: Callable[[dict], None]) -> AgentState:
    """Fill in the reply for an outcome (on_success writes the reply of a completed action)."""
    if outcome is None:
        return state
    if outcome.status == "executed":
        on_success(outcome.result or {})
        state["metadata"]["action_replayed"] = outcome.replayed
        return state

    exc = outcome.exception
    state["status"] = "error"
    if outcome.status == "failed":
        if isinstance(exc, GoogleAuthRequiredException) and "No Google account" in str(exc):
            state["result"] = GOOGLE_CONNECT_MESSAGE
        elif exc is not None and hasattr(exc, "public_message"):
            fail(state, exc)
        else:
            state["result"] = ResponseFormatter.format_error(outcome.error or "")
    elif outcome.status == "unconfirmed":
        state["result"] = ResponseFormatter.format_error(
            UNCONFIRMED.get(action_type, "I couldn't confirm whether that was done. Please check before trying again."))
        state["metadata"]["error_code"] = "action_unconfirmed"
    else:
        state["result"] = ResponseFormatter.format_error(IN_PROGRESS)
        state["metadata"]["error_code"] = "action_in_progress"
    return state
