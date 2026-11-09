"""Test helpers for approval-gated actions: an in-memory action log and a way to approve an action without the routes.

Action nodes refuse to run without an approval record, so tests that call a node directly approve it first:

    deps, state = approved(state, "send_email")      # state gets the action id; deps carries the log
    await actions.send_email_node(state, deps)
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.deps import AgentDeps
from models.ActionLogModel import EVENTS
from services import actions as actions_service


class FakeActionLog:
    """Same behaviour as models.ActionLogModel (one entry per kind per action, append-only), in memory."""

    def __init__(self):
        self.entries: list[dict] = []

    async def append(self, entry: dict) -> bool:
        assert entry["event"] in EVENTS
        if any(e["action_id"] == entry["action_id"] and e["event"] == entry["event"] for e in self.entries):
            return False
        self.entries.append({**entry, "_id": len(self.entries), "created_at": datetime.utcnow()})
        return True

    async def events(self, user_id: str, action_id: str) -> list[dict]:
        return [e for e in self.entries if e["user_id"] == user_id and e["action_id"] == action_id]

    def kinds(self, action_id: str) -> list[str]:
        return [e["event"] for e in self.entries if e["action_id"] == action_id]


async def approve(state: dict, node_name: str, log=None, user_id: str = "") -> tuple:
    """Propose and approve the state's action, the way the chat and resume routes do. Returns (deps, state)."""
    log = log or FakeActionLog()
    user_id = user_id or state.get("user_id", "")
    state["user_id"] = user_id
    proposal = await actions_service.propose(log, user_id, node_name, state.get("action_details", {}))
    await actions_service.record_decision(log, user_id, proposal["action_id"], node_name, approved=True)
    state.setdefault("metadata", {}).update(proposal)
    return AgentDeps(action_log=log), state


async def approve_paused(graph, config: dict, log, node_name: str, user_id: str) -> None:
    """The graph is paused for approval: propose and approve what it holds, as the routes do, then let it be resumed."""
    snapshot = await graph.aget_state(config)
    values = snapshot.values
    proposal = await actions_service.propose(log, user_id, node_name, values.get("action_details", {}))
    await actions_service.record_decision(log, user_id, proposal["action_id"], node_name, approved=True)
    await graph.aupdate_state(config, {"metadata": {**(values.get("metadata") or {}), **proposal}})
