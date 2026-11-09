"""Actions the user approves before they run: send email, create an event, set a reminder."""

import asyncio
import logging
from agent.deps import AgentDeps
from agent.errors import fail
from helpers.exceptions import LLMUnavailable
from agent.google_clients import calendar_for_request
from agent.google_clients import gmail_for_request
from agent.state import AgentState
from controllers.classifier import ClassifierController
from controllers.formatter import ResponseFormatter
from helpers.validation import parse_recipients
from agent.nodes.gated import execute_action, report
from helpers.exceptions import ValidationFailed
from models.db_schemes import Reminder
from services.actions import google_event_id

logger = logging.getLogger('uvicorn.error')


def extract_action_details_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Extract structured details for the action."""
    generation_client = deps.generation_client

    classifier = ClassifierController(generation_client=generation_client)
    try:
        details = classifier.extract_action_details(
            query=state["query"],
            action_type=state["action_type"],
            chat_history=state.get("chat_history", []),
            available_files=state.get("metadata", {}).get("available_files", ""),
            timezone_name=state.get("user_timezone") or None,
        )
    except LLMUnavailable as exc:
        logger.error(f"Cannot extract the action details: {exc}")
        return fail(state, exc)
    
    # Priority 5 Fix: Ensure action_details are never empty for reminder HITL
    if state["action_type"] == "reminder":
        details["task"] = details.get("task") or state["query"]
        details["priority"] = details.get("priority") or state.get("priority", "medium")
        details["date"] = details.get("date", "")
        details["time"] = details.get("time", "")

    state["action_details"] = details
    
    # Priority fix: Lift file_path to state for data_analysis_node
    if state["action_type"] == "data_analysis" and details.get("file_path"):
        state["file_path"] = details["file_path"]

    # Which fields were filled in, never their values (recipient, subject, body, attendees ...).
    logger.info(f"Action details extracted for {state['action_type']}: fields={sorted(k for k, v in details.items() if v)}")
    return state


async def send_email_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Send an email via Gmail API (runs only after the user approved this exact email; at most once)."""
    details = state.get("action_details", {})

    async def run() -> dict:
        gmail_service = await asyncio.to_thread(gmail_for_request)
        if parse_recipients(details.get("recipient", "")) is None:
            raise ValidationFailed("I couldn't find a valid recipient email address, so nothing was sent.")
        return await asyncio.to_thread(
            gmail_service.send_email,
            to=details.get("recipient", ""), subject=details.get("subject", ""), body=details.get("body", ""))

    outcome = await execute_action(state, deps, "send_email", run, result_id_key="message_id")

    def done(result: dict) -> None:
        state["result"] = ResponseFormatter.format_email_sent(result)
        state["status"] = "success"
        state["metadata"]["source"] = "gmail_send"
        state["metadata"]["message_id"] = result.get("message_id", "")

    return report(state, "send_email", outcome, done)


async def create_calendar_event_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Create a Google Calendar event (after approval). The event gets an id made from the approval, so creating
    it twice cannot give two events."""
    details = state.get("action_details", {})
    event_id = google_event_id((state.get("metadata") or {}).get("action_id", ""))

    async def run() -> dict:
        calendar_service = await asyncio.to_thread(calendar_for_request)
        return await asyncio.to_thread(
            calendar_service.create_event,
            title=details.get("title", "Meeting"), date=details.get("date", ""), time=details.get("time", "12:00"),
            attendees=details.get("attendees", []), time_zone=state.get("user_timezone") or None, event_id=event_id)

    outcome = await execute_action(state, deps, "create_calendar_event", run, idempotent=True, result_id_key="event_id")

    def done(result: dict) -> None:
        state["result"] = ResponseFormatter.format_event_created(result)
        state["status"] = "success"
        state["metadata"]["source"] = "calendar"
        state["metadata"]["event_id"] = result.get("event_id", "")

    return report(state, "create_calendar_event", outcome, done)


async def save_reminder_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """Save a reminder to MongoDB (after approval). The approval's id is unique on the reminder, so approving
    twice stores one."""
    details = state.get("action_details", {})

    async def run() -> dict:
        from models.ReminderModel import ReminderModel
        from services.reminders import compute_due_at

        reminder = Reminder(
            task=details.get("task", state["query"]),
            date=details.get("date", ""),
            time=details.get("time", ""),
            # None (no usable date/time) leaves it unscheduled; the scan then marks it so it is not retried.
            due_at=compute_due_at(details.get("date", ""), details.get("time", ""), state.get("user_timezone")),
            priority=details.get("priority", state.get("priority", "medium")),
            notes=details.get("notes", ""),
            project_id=state.get("project_id", "default"),
            user_id=state.get("user_id", ""),
            action_id=(state.get("metadata") or {}).get("action_id"),
        )
        saved = await ReminderModel(deps.db_client).create_for_action(reminder)
        return {"reminder_id": str(saved.id)}

    outcome = await execute_action(state, deps, "save_reminder", run, idempotent=True, result_id_key="reminder_id")

    def done(result: dict) -> None:
        state["result"] = ResponseFormatter.format_reminder_set(details)
        state["status"] = "success"
        state["metadata"]["source"] = "reminder"
        state["metadata"]["reminder_id"] = result.get("reminder_id", "")

    return report(state, "save_reminder", outcome, done)
