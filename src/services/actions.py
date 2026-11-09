"""Approval-gated actions: the audit trail and the guarantee that an approved action happens at most once.

An action (one email, one calendar event, one reminder, one Drive file) has an id (its idempotency key), made when
the assistant proposes it. Everything about it is appended to the audit log (models/ActionLogModel.py):

    proposed -> approved | rejected -> executing -> executed | failed | unknown

`run_once` is the only way a node performs an action. It refuses to run without an approval record, refuses if the
parameters differ from what was approved (the hash taken at proposal), claims the execution with an `executing`
entry *before* calling the outside service, and records the id the service returns (message id, event id ...)
*before* the node reports success.

Retries: asking again with the same action id never repeats the call.
  - already executed: the stored result is returned;
  - a call that may or may not have happened (a crash or timeout between "the service accepted it" and "we recorded
    it"): for an email or a Drive file this is `unknown` and is never retried blindly, because a duplicate email
    cannot be taken back; the user is told to check;
  - actions that are safe to repeat (`idempotent=True`: a calendar event created under a fixed id, a reminder saved
    under a unique action id) are simply run again.
"""
import base64
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from helpers.exceptions import ExternalServiceError, GoogleAuthRequiredException, ValidationFailed

logger = logging.getLogger("uvicorn.error")

# Another attempt that started less than this long ago may still be running; an older one has died.
EXECUTING_LEASE_SECONDS = 120
# A proposal nobody answered for this long is shown as expired in the user's log.
PROPOSAL_EXPIRES_AFTER = timedelta(hours=24)


class ActionNotApproved(Exception):
    """There is no approval record for this action, or what is about to run is not what was approved."""


@dataclass
class ActionOutcome:
    status: str                              # executed | failed | unconfirmed | in_progress
    result: Optional[dict] = None            # what the service returned (on `executed`)
    error: Optional[str] = None              # a safe message (on `failed`)
    exception: Optional[Exception] = None    # the live exception, when this attempt raised it (not on a replay)
    replayed: bool = False                   # True when this is the stored outcome of an earlier attempt


def params_hash(action_type: str, params: dict) -> str:
    """A fingerprint of exactly what the user approves. Key order and spacing do not matter; any other change does."""
    canonical = json.dumps({"type": action_type, "params": params or {}}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def google_event_id(action_id: str) -> str:
    """A Google Calendar event id made from the action id (Google wants lowercase a-v and 0-9, 5 to 1024 long)."""
    digest = hashlib.sha256(("nexusai-event:" + action_id).encode()).digest()
    return base64.b32hexencode(digest).decode().lower().rstrip("=")


def _clip(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def summarize(action_type: str, params: dict) -> dict:
    """What the user's log shows about an action: who or what, never message bodies or document text."""
    params = params or {}
    if action_type == "send_email":
        return {"to": _clip(params.get("recipient"), 200), "subject": _clip(params.get("subject"), 150)}
    if action_type == "create_calendar_event":
        return {"title": _clip(params.get("title"), 150), "date": _clip(params.get("date"), 20),
                "time": _clip(params.get("time"), 10)}
    if action_type == "save_reminder":
        return {"task": _clip(params.get("task"), 150), "date": _clip(params.get("date"), 20),
                "time": _clip(params.get("time"), 10)}
    if action_type == "drive_save":
        return {"operation": _clip(params.get("operation") or "create_document", 30),
                "name": _clip(params.get("title") or params.get("file_name"), 150)}
    return {}


async def propose(log, user_id: str, action_type: str, params: dict) -> dict:
    """Record that the assistant asked for approval. Returns {"action_id", "params_hash"} for the checkpoint."""
    action_id = uuid.uuid4().hex
    digest = params_hash(action_type, params)
    await log.append({"user_id": user_id, "action_id": action_id, "action_type": action_type, "event": "proposed",
                      "params_hash": digest, "summary": summarize(action_type, params)})
    return {"action_id": action_id, "params_hash": digest}


async def record_decision(log, user_id: str, action_id: str, action_type: str, approved: bool) -> None:
    """The user's answer: who approved (or rejected) and when. Answering the same action twice keeps the first answer."""
    if not action_id:  # a request paused before actions were logged: nothing to record, and the node will refuse it
        return
    now = datetime.utcnow()
    entry = {"user_id": user_id, "action_id": action_id, "action_type": action_type,
             "event": "approved" if approved else "rejected", "approver": user_id}
    entry["approved_at" if approved else "rejected_at"] = now
    await log.append(entry)


def _definitely_not_done(exc: Exception) -> bool:
    """Failures that happen before the outside service acts: bad input, a rejected token, or a 4xx answer."""
    if isinstance(exc, (ValidationFailed, GoogleAuthRequiredException)):
        return True
    return isinstance(exc, ExternalServiceError) and 400 <= exc.http_status < 500


def _by_event(events: list) -> dict:
    return {e["event"]: e for e in events}


async def run_once(log, user_id: str, action_id: str, action_type: str, params: dict,
                   run: Callable[[], Awaitable[dict]], idempotent: bool = False,
                   result_id_key: str = "", now: Optional[datetime] = None) -> ActionOutcome:
    """Perform an approved action at most once (see the module docstring). `run` makes the outside call and returns
    the service's answer as a dict; `result_id_key` names the field holding its id. Raises ActionNotApproved."""
    now = now or datetime.utcnow()
    events = _by_event(await log.events(user_id, action_id))

    if "proposed" not in events or "approved" not in events:
        raise ActionNotApproved("no approval record for this action")
    if events["proposed"].get("params_hash") != params_hash(action_type, params):
        if "failed" not in events:
            await log.append({"user_id": user_id, "action_id": action_id, "action_type": action_type, "event": "failed",
                              "error": "The details changed after you approved them, so nothing was done."})
        raise ActionNotApproved("the parameters differ from what was approved")

    if "executed" in events:
        return ActionOutcome("executed", result=events["executed"].get("result"), replayed=True)
    if "failed" in events:
        return ActionOutcome("failed", error=events["failed"].get("error"), replayed=True)
    if "unknown" in events:
        return ActionOutcome("unconfirmed", replayed=True)

    if "executing" in events:
        # An earlier attempt got as far as starting the call and then neither finished nor failed cleanly.
        if not idempotent:
            if now - events["executing"]["created_at"] < timedelta(seconds=EXECUTING_LEASE_SECONDS):
                return ActionOutcome("in_progress", replayed=True)
            await log.append({"user_id": user_id, "action_id": action_id, "action_type": action_type, "event": "unknown"})
            logger.warning(f"Action {action_id} ({action_type}) may or may not have happened; not repeating it")
            return ActionOutcome("unconfirmed", replayed=True)
        # idempotent: running it again cannot duplicate it
    else:
        claimed = await log.append({"user_id": user_id, "action_id": action_id, "action_type": action_type,
                                    "event": "executing"})
        if not claimed:  # another request claimed it a moment ago
            return ActionOutcome("in_progress", replayed=True)

    try:
        result = await run()
    except Exception as exc:
        if _definitely_not_done(exc):
            message = exc.public_message if hasattr(exc, "public_message") else "The action could not be completed."
            await log.append({"user_id": user_id, "action_id": action_id, "action_type": action_type, "event": "failed",
                              "error": message})
            return ActionOutcome("failed", error=message, exception=exc)
        logger.error(f"Action {action_id} ({action_type}) ended in an unclear state: {type(exc).__name__}")
        if idempotent:   # the log stays at `executing`; approving again runs it again, safely
            return ActionOutcome("failed", error="It could not be completed. Please try again.", exception=exc)
        await log.append({"user_id": user_id, "action_id": action_id, "action_type": action_type, "event": "unknown"})
        return ActionOutcome("unconfirmed", exception=exc)

    # The service did it. Record its id *before* anything reports success.
    await log.append({"user_id": user_id, "action_id": action_id, "action_type": action_type, "event": "executed",
                      "executed_at": datetime.utcnow(), "result_id": str(result.get(result_id_key, "")) if result_id_key else "",
                      "result": result})
    return ActionOutcome("executed", result=result)


# ── reading the log ─────────────────────────────────────────────────────────

_STATUS_BY_LAST_EVENT = {
    "proposed": "awaiting_approval", "approved": "approved", "rejected": "rejected",
    "executing": "unconfirmed", "executed": "done", "failed": "failed", "unknown": "unconfirmed",
}


# Where each entry sits in an action's life. The status comes from the furthest stage reached, not from timestamps,
# which can tie (a clock only ticks so often) and would then show the wrong status.
_STAGE = {"proposed": 0, "approved": 1, "rejected": 1, "executing": 2, "executed": 3, "failed": 3, "unknown": 3}


def history(events: list, now: Optional[datetime] = None, limit: int = 50) -> list[dict]:
    """Group log entries (newest first, as `recent_events` returns them) into one row per action for the user's log."""
    now = now or datetime.utcnow()
    grouped: dict[str, list] = {}
    for entry in events:
        grouped.setdefault(entry["action_id"], []).append(entry)
    rows = []
    for action_id, entries in grouped.items():
        by_event = {e["event"]: e for e in entries}
        last = max(entries, key=lambda e: (_STAGE[e["event"]], e["created_at"]))
        status = _STATUS_BY_LAST_EVENT[last["event"]]
        if status == "awaiting_approval" and now - last["created_at"] > PROPOSAL_EXPIRES_AFTER:
            status = "expired"
        rows.append({
            "action_id": action_id,
            "action_type": last.get("action_type") or by_event.get("proposed", {}).get("action_type", ""),
            "summary": by_event.get("proposed", {}).get("summary", {}),
            "status": status,
            "proposed_at": _iso(by_event.get("proposed", {}).get("created_at")),
            "decided_at": _iso((by_event.get("approved") or by_event.get("rejected") or {}).get("created_at")),
            "executed_at": _iso(by_event.get("executed", {}).get("executed_at")),
            "result_id": by_event.get("executed", {}).get("result_id", ""),
            "error": (by_event.get("failed") or {}).get("error", ""),
            "updated_at": _iso(last["created_at"]),
        })
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows[:limit]


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None
