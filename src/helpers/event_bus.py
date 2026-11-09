"""One-time tickets that let the browser's EventSource open the notifications stream.

EventSource cannot send an Authorization header, so a logged-in page asks for a ticket and passes it in the URL.
Tickets live a minute and work once, so they are kept in memory: losing them in a restart only means the page asks
for a new one. (The notifications themselves are stored in MongoDB, see services/notifier.py.)
"""
import secrets
import time
from typing import Optional

_TICKET_TTL_SECONDS = 60
_tickets: dict[str, tuple[str, float]] = {}


def issue_ticket(user_id: str) -> str:
    now = time.time()
    for key in [k for k, (_, exp) in _tickets.items() if exp < now]:
        del _tickets[key]
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (user_id, now + _TICKET_TTL_SECONDS)
    return ticket


def redeem_ticket(ticket: str) -> Optional[str]:
    entry = _tickets.pop(ticket, None)
    if entry is None or entry[1] < time.time():
        return None
    return entry[0]


def forget_user(user_id: str) -> None:
    """Drop the user's unused stream tickets (account deletion)."""
    for ticket in [t for t, (owner, _) in _tickets.items() if owner == user_id]:
        del _tickets[ticket]
