"""End-to-end smoke test against a RUNNING local backend, with throwaway accounts.

    python scripts/e2e_smoke.py [--base http://localhost:5000] [--wait-extra 60]

Start the backend first (see CLAUDE.md, section 3). The script needs a working LLM and embedding key in
src/.env (the chat and knowledge-base steps call them). It talks to the real HTTP API, nothing is mocked.

What it checks (each line prints PASS / FAIL / SKIP):
  1. two sign-ups (the second user proves isolation)
  2. chat: a reminder request pauses for approval, the user approves, the reminder is saved
  3. the reminder is delivered to the app as a notification (REST list and the SSE stream) within 60 seconds
     of its due time, and is delivered once
  4. knowledge base: upload, process, index and answer from the uploaded text
  5. cross-user isolation: the second user sees none of the first user's notifications, jobs, reminders or documents
  6. sign-out: the refresh token stops working

It leaves the two throwaway accounts (email smoke-*@example.com) and their data in the database; account
deletion does not exist yet (PR 0.6). Exit code is 0 only if nothing FAILed. It never touches Google or WhatsApp,
so it proves nothing about real Google accounts.
"""
import argparse
import os
import io
import json
import re
import subprocess
import zipfile
import secrets
import sys
import threading
from pathlib import Path
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

results: list[tuple[str, str, str]] = []
LOG_START = [0]   # size of the server log when this run began


def squash(text: str) -> str:
    """Letters and digits only, lower case: the model may re-capitalise or space out a marker we put in a request."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def record(status: str, name: str, detail: str = "") -> bool:
    results.append((status, name, detail))
    print(f"{status:5} {name}" + (f"  - {detail}" if detail else ""), flush=True)
    return status == "PASS"


def check(name: str, ok: bool, detail: str = "") -> bool:
    return record("PASS" if ok else "FAIL", name, "" if ok else detail)


class Api:
    """One signed-in user, using the bearer token (API clients do not need the CSRF header)."""

    def __init__(self, base: str, label: str):
        self.base = base.rstrip("/")
        self.label = label
        self.email = f"smoke-{label}-{uuid.uuid4().hex[:8]}@example.com"
        self.password = "Smk-" + secrets.token_urlsafe(14) + "9!"
        self.http = requests.Session()
        self.token = ""

    def call(self, method: str, path: str, **kwargs) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        kwargs.setdefault("timeout", 120)
        return self.http.request(method, self.base + path, headers=headers, **kwargs)

    def sign_up(self) -> requests.Response:
        response = self.http.post(self.base + "/api/v1/auth/local/signup", timeout=60,
                                  json={"name": f"Smoke {self.label}", "email": self.email, "password": self.password})
        if response.ok:
            self.token = response.json()["access_token"]
        return response


def stream_notifications(api: Api, seen: list, stop: threading.Event, errors: list) -> None:
    """Read the SSE stream and collect the `notification` events (what the web app receives).

    The stream is silent while nothing happens, so a read timeout is normal: reconnect with a fresh ticket
    (tickets are single use), as the web app does. Unread notifications are sent again on a new stream."""
    while not stop.is_set():
        try:
            ticket = api.call("POST", "/api/v1/agent/notifications/ticket").json()["ticket"]
            with requests.get(f"{api.base}/api/v1/agent/notifications", params={"ticket": ticket},
                              stream=True, timeout=(10, 8)) as response:
                event = None
                for raw in response.iter_lines(decode_unicode=True):
                    if stop.is_set():
                        return
                    if raw.startswith("event:"):
                        event = raw.split(":", 1)[1].strip()
                    elif raw.startswith("data:") and event == "notification":
                        seen.append((time.time(), json.loads(raw.split(":", 1)[1])))
                        event = None
        except requests.exceptions.RequestException:
            continue  # idle timeout: reconnect
        except Exception as exc:
            errors.append(str(exc))
            return


def step_reminder(alice: Api, wait_extra: int) -> None:
    marker = "smoke" + uuid.uuid4().hex[:6]
    alice.call("PUT", "/api/v1/auth/local/timezone", json={"timezone": "UTC"})

    seen: list = []
    errors: list = []
    stop = threading.Event()
    thread = threading.Thread(target=stream_notifications, args=(alice, seen, stop, errors), daemon=True)
    thread.start()

    # Due at the next whole minute that is at least 30 seconds away (the app works in minutes).
    due = (datetime.now(timezone.utc) + timedelta(seconds=30)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    query = f"remind me to {marker} on {due:%Y-%m-%d} at {due:%H:%M}"
    chat = alice.call("POST", "/api/v1/agent/chat", json={"query": query, "thread_id": "smoke" + marker})
    body = chat.json() if chat.ok else {}
    if not check("chat: a reminder request pauses for approval",
                 chat.ok and body.get("status") == "pending_approval" and body.get("type") == "action:reminder",
                 f"HTTP {chat.status_code}, status={body.get('status')!r}, type={body.get('type')!r}"):
        stop.set()
        return
    details = body.get("action_details", {})
    check("chat: the proposed reminder has the right date and time",
          details.get("date") == f"{due:%Y-%m-%d}" and details.get("time") == f"{due:%H:%M}",
          f"model proposed date={details.get('date')!r} time={details.get('time')!r}, wanted {due:%Y-%m-%d} {due:%H:%M}")

    approve = alice.call("POST", "/api/v1/agent/resume", json={
        "thread_id": body["thread_id"], "approve": True, "resume_token": body["metadata"].get("resume_token")})
    check("approval: the reminder is saved", approve.ok and approve.json().get("status") == "success",
          f"HTTP {approve.status_code} {approve.text[:200]}")
    replay = alice.call("POST", "/api/v1/agent/resume", json={
        "thread_id": body["thread_id"], "approve": True, "resume_token": body["metadata"].get("resume_token")})
    check("approval: approving the same action again does nothing", not replay.ok, f"HTTP {replay.status_code}")
    saved = [r for r in alice.call("GET", "/api/v1/reminders").json().get("data", {}).get("reminders", [])
             if marker in squash(r.get("task", ""))]
    check("approval: exactly one reminder was saved", len(saved) == 1, f"{len(saved)} reminders")
    log = alice.call("GET", "/api/v1/actions/log").json()["actions"]
    row = next((r for r in log if r["action_id"] == body["metadata"].get("action_id")), None)
    check("audit log: the action shows proposed, approved by you and done, with the reminder id",
          row is not None and row["status"] == "done" and row["action_type"] == "save_reminder"
          and row["decided_at"] and row["executed_at"] and row["result_id"] and marker in squash(str(row["summary"])),
          f"row={row}")

    # Delivery: wait until due + 60 s (the acceptance limit), watching both the stream and the REST list.
    deadline = due.timestamp() + 60 + wait_extra
    delivered = None
    while time.time() < deadline + 1:
        listed = alice.call("GET", "/api/v1/notifications").json().get("notifications", [])
        delivered = next((n for n in listed if marker in squash(n["title"])), None)
        if delivered and any(marker in squash(n["title"]) for _, n in seen):
            break
        time.sleep(2)
    stop.set()
    thread.join(timeout=10)

    if not check("delivery: the reminder is stored as a notification", delivered is not None,
                 "no notification appeared before the deadline"):
        return
    streamed = [(at, n) for at, n in seen if marker in squash(n["title"])]
    check("delivery: it arrives on the live stream", bool(streamed), "; ".join(errors) or "no SSE event received")
    # The server stamps the notification when the scan creates it: that is the delivery time.
    created = datetime.fromisoformat(delivered["created_at"].rstrip("Z")).replace(tzinfo=timezone.utc)
    late = (created - due).total_seconds()
    check("delivery: within 60 seconds of its due time", 0 <= late <= 60, f"{late:.0f}s after the due time")
    record("INFO", "delivery delay", f"notification created {late:.1f}s after the due time")
    if streamed:
        record("INFO", "stream delay", f"web app received it {streamed[0][0] - due.timestamp():.1f}s after the due time")

    time.sleep(25)  # more than one scan interval: it must not come twice
    again = [n for n in alice.call("GET", "/api/v1/notifications").json()["notifications"] if marker in squash(n["title"])]
    check("delivery: exactly once", len(again) == 1, f"{len(again)} copies")

    read = alice.call("POST", f"/api/v1/notifications/{delivered['id']}/read")
    unread = alice.call("GET", "/api/v1/notifications", params={"unread_only": "true"}).json()["notifications"]
    check("notification can be marked read", read.ok and all(marker not in squash(n["title"]) for n in unread))
    return delivered["id"]


def step_knowledge_base(alice: Api, bob: Api) -> str:
    code = "vault-" + uuid.uuid4().hex[:8]
    text = f"Company handbook.\nThe secret door code for the Zephyr office is {code}.\nLunch is at noon.\n"
    upload = alice.call("POST", "/api/v1/data/upload/default",
                        files={"file": ("handbook.txt", text.encode(), "text/plain")})
    if not check("knowledge base: upload", upload.ok, f"HTTP {upload.status_code} {upload.text[:200]}"):
        return code
    file_id = upload.json()["file_id"]
    process = alice.call("POST", "/api/v1/data/process/default", json={"file_id": file_id, "do_reset": True})
    check("knowledge base: process into chunks", process.ok, f"HTTP {process.status_code} {process.text[:200]}")
    index = alice.call("POST", "/api/v1/nlp/index/push/default", json={"do_reset": True})
    check("knowledge base: index", index.ok, f"HTTP {index.status_code} {index.text[:200]}")
    answer = alice.call("POST", "/api/v1/nlp/index/answer/default",
                        json={"text": "What is the secret door code for the Zephyr office?", "limit": 3})
    check("knowledge base: the answer contains the uploaded fact",
          answer.ok and code in answer.json().get("answer", ""), f"HTTP {answer.status_code} {answer.text[:200]}")
    return code


def step_rejection(alice: Api, bob: Api) -> None:
    marker = "reject" + uuid.uuid4().hex[:6]
    chat = alice.call("POST", "/api/v1/agent/chat", json={
        "query": f"remind me to {marker} on 2030-01-15 at 10:00", "thread_id": "rej" + marker})
    body = chat.json() if chat.ok else {}
    if not check("rejection: a reminder request pauses for approval", body.get("status") == "pending_approval",
                 f"HTTP {chat.status_code} {chat.text[:150]}"):
        return
    no = alice.call("POST", "/api/v1/agent/resume", json={
        "thread_id": body["thread_id"], "approve": False, "resume_token": body["metadata"].get("resume_token")})
    check("rejection: the user can say no", no.ok and no.json().get("status") == "rejected", f"HTTP {no.status_code}")
    saved = [r for r in alice.call("GET", "/api/v1/reminders").json().get("data", {}).get("reminders", [])
             if marker in squash(r.get("task", ""))]
    check("rejection: nothing was saved", saved == [], f"{len(saved)} reminders")
    log = alice.call("GET", "/api/v1/actions/log").json()["actions"]
    row = next((r for r in log if r["action_id"] == body["metadata"].get("action_id")), None)
    check("audit log: a rejected action is recorded as rejected", row is not None and row["status"] == "rejected",
          f"row={row}")
    check("audit log: the second user sees none of the first user's actions",
          bob.call("GET", "/api/v1/actions/log").json()["actions"] == [])


def step_channels(alice: Api, bob: Api) -> None:
    prefs = alice.call("GET", "/api/v1/notifications/preferences").json()
    check("channels: optional channels are off by default", prefs["push"] is False and prefs["email"] is False)
    no_google = alice.call("PUT", "/api/v1/notifications/preferences", json={"email": True})
    check("channels: email-to-self is refused until Google is connected", no_google.status_code == 409,
          f"HTTP {no_google.status_code}")
    if prefs["push_available"]:
        on = alice.call("PUT", "/api/v1/notifications/preferences", json={"push": True})
        check("channels: push can be switched on (server has VAPID keys)", on.ok and on.json()["push"] is True)
        check("channels: the preference is per user",
              bob.call("GET", "/api/v1/notifications/preferences").json()["push"] is False)
        alice.call("PUT", "/api/v1/notifications/preferences", json={"push": False})
    else:
        record("SKIP", "channels: push", "the server has no VAPID keys (python scripts/generate_vapid_keys.py)")
        refused = alice.call("PUT", "/api/v1/notifications/preferences", json={"push": True})
        check("channels: push is refused when the server cannot deliver it", refused.status_code == 409,
              f"HTTP {refused.status_code}")
    sent = alice.call("POST", "/api/v1/notifications/test").json()
    titles = [n["title"] for n in alice.call("GET", "/api/v1/notifications", params={"unread_only": "true"})
              .json()["notifications"]]
    check("channels: a test notification is stored and waits unread", sent.get("sent") and "✅ Test notification" in titles)
    # Connect a stream *after* it was created: what a user who was offline sees on return.
    seen: list = []
    stop = threading.Event()
    thread = threading.Thread(target=stream_notifications, args=(alice, seen, stop, []), daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline and not any("Test notification" in n["title"] for _, n in seen):
        time.sleep(0.5)
    stop.set()
    thread.join(timeout=10)
    check("offline: a notification created before connecting arrives when the app connects",
          any("Test notification" in n["title"] for _, n in seen))


def step_usage_and_limits(alice: Api, bob: Api) -> None:
    mine = alice.call("GET", "/api/v1/usage").json()
    today = mine["today"]
    check("usage: the model calls of the chat are counted for the user",
          today["llm_calls"] >= 1 and today["input_tokens"] > 0 and today["output_tokens"] > 0, f"today={today}")
    check("usage: chat and approvals are counted", today["chat_requests"] >= 2 and today["action_requests"] >= 2,
          f"today={today}")
    check("usage: the knowledge-base embeddings are counted", today["embedding_tokens"] > 0, f"today={today}")
    check("usage: the estimated cost and the daily ceiling are shown",
          0 < mine["daily_ceiling_usd"] and 0 <= mine["ceiling_used_fraction"] < 1 and not mine["paused"], f"{mine}")
    check("usage: the second user's numbers are their own",
          bob.call("GET", "/api/v1/usage").json()["today"]["llm_calls"] == 0)

    # A burst of chat requests with an empty body: they are refused as invalid, but each still counts toward the
    # per-user limit, so this reaches it without spending any model calls.
    statuses = [alice.call("POST", "/api/v1/agent/chat", json={}).status_code for _ in range(30)]
    over = alice.call("POST", "/api/v1/agent/chat", json={})
    check("limits: chatting too fast is stopped with 429", 429 in statuses and over.status_code == 429,
          f"statuses={sorted(set(statuses))}")
    check("limits: the answer says how long to wait", over.headers.get("Retry-After", "").isdigit()
          and "too quickly" in over.json().get("detail", ""), f"{over.headers.get('Retry-After')} {over.text[:150]}")
    check("limits: another user is not affected",
          bob.call("POST", "/api/v1/agent/chat", json={}).status_code == 422)
    check("limits: reminders and notifications keep working while chat is limited",
          alice.call("GET", "/api/v1/notifications").status_code == 200)


def step_email_verification(alice: Api, server_log: str) -> None:
    check("verification: a new password account starts unconfirmed",
          alice.call("GET", "/api/v1/auth/local/me").json()["user"]["email_verified"] is False)
    check("verification: a confirmed-looking token that was never issued is refused",
          alice.call("POST", "/api/v1/auth/local/verify-email", json={"token": "x" * 40}).status_code == 400)
    if not server_log:
        record("SKIP", "verification: the emailed link", "pass --server-log with the file the server writes to (development logs the link)")
        return
    with open(server_log, "rb") as handle:       # only what the server wrote since this run began; alice signed up first
        handle.seek(LOG_START[0])
        text = handle.read().decode("utf-8", errors="replace")
    links = re.findall(r"verify-email\?token=([A-Za-z0-9_\-]+)", text)
    if not check("verification: the link was written to the server log", bool(links), "no link in the log"):
        return
    token = links[0]
    done = alice.call("POST", "/api/v1/auth/local/verify-email", json={"token": token})
    check("verification: the link confirms the address", done.ok and done.json() == {"verified": True}, f"HTTP {done.status_code}")
    check("verification: it is now confirmed", alice.call("GET", "/api/v1/auth/local/me").json()["user"]["email_verified"] is True)
    check("verification: the link works only once",
          alice.call("POST", "/api/v1/auth/local/verify-email", json={"token": token}).status_code == 400)


def step_export(alice: Api) -> None:
    response = alice.call("GET", "/api/v1/account/export")
    if not check("export: a ZIP is returned", response.ok and response.headers.get("content-type") == "application/zip",
                 f"HTTP {response.status_code}"):
        return
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    data = json.loads(archive.read("nexusai-export/export.json"))
    names = archive.namelist()
    check("export: it holds the user's reminders, knowledge-base file and account details",
          data["account"]["email"] == alice.email and any(squash(r["task"]).startswith("smoke") for r in data["reminders"])
          and any(n.endswith("handbook.txt") for n in names), f"names={names}")
    blob = response.content + b"".join(archive.read(n) for n in names)
    check("export: no password hash or Google token is included", b"password_hash" not in blob and b"google_token" not in blob)


def step_delete_account(base: str, wait_extra: int) -> None:
    carol = Api(base, "c")
    if not check("account deletion: a throwaway user signs up", carol.sign_up().ok):
        return
    carol.call("POST", "/api/v1/data/upload/default", files={"file": ("secret-notes.txt", b"carol private notes " * 5, "text/plain")})
    carol.call("POST", "/api/v1/notifications/test")
    carol.call("PUT", "/api/v1/auth/local/timezone", json={"timezone": "UTC"})
    user_id = json.loads(zipfile.ZipFile(io.BytesIO(carol.call("GET", "/api/v1/account/export").content))
                         .read("nexusai-export/export.json"))["account"]["id"]

    wrong = carol.call("POST", "/api/v1/account/delete", json={"password": "not my password"})
    check("account deletion: the wrong password deletes nothing", wrong.status_code == 403
          and carol.call("GET", "/api/v1/auth/local/me").ok, f"HTTP {wrong.status_code}")
    done = carol.call("POST", "/api/v1/account/delete", json={"password": carol.password})
    check("account deletion: the right password deletes the account",
          done.ok and done.json().get("deleted") is True, f"HTTP {done.status_code} {done.text[:150]}")
    again = requests.post(base + "/api/v1/auth/local/signin", json={"email": carol.email, "password": carol.password}, timeout=60)
    check("account deletion: signing in no longer works", again.status_code == 401, f"HTTP {again.status_code}")
    check("account deletion: the old session no longer works", carol.call("GET", "/api/v1/auth/local/me").status_code == 401)

    script = Path(__file__).resolve().parent / "verify_account_deleted.py"
    result = subprocess.run([sys.executable, str(script), user_id, "--email", carol.email], capture_output=True, text=True, timeout=300)
    check("account deletion: verify_account_deleted.py finds nothing left of the user",
          result.returncode == 0 and "Clean" in result.stdout, (result.stdout + result.stderr)[-400:])


def step_isolation(alice: Api, bob: Api, alice_notification_id, code: str) -> None:
    check("isolation: the second user sees no notifications of the first",
          bob.call("GET", "/api/v1/notifications").json()["notifications"] == [])
    if alice_notification_id:
        check("isolation: the second user cannot mark the first user's notification",
              bob.call("POST", f"/api/v1/notifications/{alice_notification_id}/read").status_code == 404)
    check("isolation: the second user sees no reminders of the first",
          bob.call("GET", "/api/v1/reminders").json().get("data", {}).get("reminders") == [])
    answer = bob.call("POST", "/api/v1/nlp/index/answer/default",
                      json={"text": "What is the secret door code for the Zephyr office?", "limit": 3})
    check("isolation: the second user's knowledge base does not contain the first user's document",
          code not in (answer.text if answer.ok else ""), "the code leaked")
    check("isolation: an unauthenticated request is refused",
          requests.get(alice.base + "/api/v1/notifications", timeout=30).status_code == 401)


def step_sign_out(base: str, api: Api) -> None:
    session = requests.Session()
    signin = session.post(base + "/api/v1/auth/local/signin", json={"email": api.email, "password": api.password},
                          timeout=60)
    if not check("sign-in with the password", signin.ok, f"HTTP {signin.status_code}"):
        return
    refresh_cookie = session.cookies.get("refresh_token", path="/api/v1/auth/local")
    check("sign-in: a session cookie was set", refresh_cookie is not None)
    out = session.post(base + "/api/v1/auth/local/signout", timeout=30)
    check("sign-out", out.ok, f"HTTP {out.status_code}")
    stale = requests.post(base + "/api/v1/auth/local/refresh", timeout=30,
                          cookies={"refresh_token": refresh_cookie} if refresh_cookie else None)
    check("sign-out: the old refresh token no longer works", stale.status_code == 401, f"HTTP {stale.status_code}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:5000")
    parser.add_argument("--server-log", default="",
                        help="the file the server writes its log to; development logs the verification link there")
    parser.add_argument("--wait-extra", type=int, default=30,
                        help="seconds to keep waiting after the 60-second delivery limit before giving up")
    args = parser.parse_args()

    try:
        requests.get(args.base + "/health", timeout=10).raise_for_status()
    except Exception as exc:
        print(f"The backend is not reachable at {args.base}: {exc}")
        return 2

    if args.server_log:
        LOG_START[0] = os.path.getsize(args.server_log)
    alice, bob = Api(args.base, "a"), Api(args.base, "b")
    for user in (alice, bob):
        response = user.sign_up()
        if not check(f"sign-up of user {user.label}", response.ok, f"HTTP {response.status_code} {response.text[:200]}"):
            return 1

    notification_id = step_reminder(alice, args.wait_extra)
    code = step_knowledge_base(alice, bob)
    step_rejection(alice, bob)
    step_channels(alice, bob)
    step_isolation(alice, bob, notification_id, code)
    step_usage_and_limits(alice, bob)
    step_email_verification(alice, args.server_log)
    step_export(alice)
    step_delete_account(args.base, args.wait_extra)
    step_sign_out(args.base, alice)

    failed = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len([r for r in results if r[0] == 'PASS'])} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
