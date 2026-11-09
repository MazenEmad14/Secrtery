import asyncio
import hashlib
import logging

from controllers.llm_schemas import UrgentVerdict
from helpers.exceptions import LLMOutputError
from helpers.llm_json import llm_json
from helpers.prompts import render
from models.UserModel import UserModel
from services.notifier import notify
from services.usage import as_user
from stores.gmail.GmailService import GmailService

logger = logging.getLogger("uvicorn.error")

# Message ids already evaluated per user, so an unchanged inbox costs no LLM call.
_seen_message_ids: dict[str, set[str]] = {}
_MAX_SEEN_PER_USER = 500



def _fetch_unread(token: dict):
    gmail = GmailService()
    refreshed = gmail.authenticate_from_dict(token)
    emails = gmail.search_emails(query="is:unread", max_results=5)
    return emails, refreshed


async def _evaluate_user(services, user: dict):
    user_id = user["user_id"]
    emails, refreshed = await asyncio.to_thread(_fetch_unread, user["google_token"])

    if refreshed and refreshed != user["google_token"]:
        user_model = await UserModel.create_instance(services.mongo_db)
        await user_model.update_google_token(user["email"], refreshed)

    seen = _seen_message_ids.setdefault(user_id, set())
    new_emails = [e for e in emails or [] if e.get("message_id") not in seen]
    if not new_emails:
        return
    if len(seen) > _MAX_SEEN_PER_USER:
        seen.clear()
    seen.update(e.get("message_id") for e in new_emails)

    generation_client = getattr(services, "generation_client", None)
    if generation_client is None:
        return

    emails_text = ""
    for i, email in enumerate(new_emails):
        sender = email.get("sender") or email.get("from", "Unknown")
        emails_text += f"Email {i+1}:\nSubject: {email.get('subject', '')}\nSnippet: {email.get('snippet', '')}\nFrom: {sender}\n\n"

    try:
        verdict = await llm_json(
            generation_client,
            render("proactive", "urgent_check", emails_text=emails_text),
            UrgentVerdict,
            chat_history=[],
            max_output_tokens=500,
            temperature=0.1,
            json_mode=True,
        )
    except LLMOutputError as exc:
        logger.error(f"Proactive agent reply unusable: {exc}")
        return

    if verdict.is_urgent and verdict.message:
        # The same emails give the same key, so a restart (which forgets `_seen_message_ids`) cannot alert twice.
        ids = sorted(str(e.get("message_id")) for e in new_emails)
        key = "inbox_alert:" + user_id + ":" + hashlib.sha1("|".join(ids).encode()).hexdigest()
        await notify(services.mongo_db, user_id, kind="inbox_alert", title="📬 Urgent email",
                     body=verdict.message, data={"message_ids": ids}, dedupe_key=key)
        logger.info(f"Proactive alert stored for user {user_id}")


async def evaluate_inbox_and_notify(services):
    """Check each Google-connected user's inbox with that user's own token."""
    try:
        user_model = await UserModel.create_instance(services.mongo_db)
        users = await user_model.list_google_users()
    except Exception:
        logger.error("Proactive agent could not list users", exc_info=True)
        return

    for user in users:
        try:
            with as_user(user["user_id"]):   # the model calls of this check are this user's
                await _evaluate_user(services, user)
        except Exception as exc:
            # One user's expired token or API error must not stop the others.
            logger.warning(f"Proactive check failed for user {user['user_id']}: {exc}")
