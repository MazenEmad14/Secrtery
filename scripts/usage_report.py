"""What each user used, from the `usage` collection (shadow metering): the numbers behind "what does a user cost".

    python scripts/usage_report.py [--days 30] [--user USER_ID]

Reads the database named in src/.env (MONGODB_URL / MONGODB_DATABASE). Read-only. Costs are estimates: tokens are
estimated from text length and priced with the assumptions in the settings (see services/usage.py), so treat the
dollar figures as a guide until they are checked against a real invoice.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from pymongo import MongoClient  # noqa: E402

from helpers.config import get_settings  # noqa: E402

FIELDS = ("chat_requests", "action_requests", "llm_calls", "input_tokens", "output_tokens", "embedding_tokens",
          "transcription_seconds", "whatsapp_messages", "est_cost_usd")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--user", help="only this user id")
    args = parser.parse_args()

    settings = get_settings()
    db = MongoClient(settings.MONGODB_URL)[settings.MONGODB_DATABASE]
    first = (datetime.now(timezone.utc) - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    match = {"day": {"$gte": first}}
    if args.user:
        match["user_id"] = args.user
    totals: dict[str, dict] = {}
    days_active: dict[str, int] = {}
    for doc in db["usage"].find(match):
        user = totals.setdefault(doc["user_id"], {name: 0 for name in FIELDS})
        days_active[doc["user_id"]] = days_active.get(doc["user_id"], 0) + 1
        for name in FIELDS:
            user[name] += doc.get(name, 0)

    if not totals:
        print(f"No usage recorded since {first}.")
        return 0
    print(f"Usage since {first} ({settings.USAGE_METERING} metering; costs are estimates)\n")
    header = f"{'user':<26}{'days':>5}{'chats':>7}{'actions':>8}{'llm calls':>10}{'tokens in':>11}{'tokens out':>11}{'audio s':>9}{'wa msgs':>8}{'est $':>9}"
    print(header)
    print("-" * len(header))
    ranked = sorted(totals.items(), key=lambda item: item[1]["est_cost_usd"], reverse=True)
    for user_id, t in ranked:
        print(f"{user_id[:24]:<26}{days_active[user_id]:>5}{t['chat_requests']:>7}{t['action_requests']:>8}{t['llm_calls']:>10}"
              f"{t['input_tokens']:>11}{t['output_tokens']:>11}{t['transcription_seconds']:>9.0f}{t['whatsapp_messages']:>8}"
              f"{t['est_cost_usd']:>9.4f}")
    cost = sum(t["est_cost_usd"] for t in totals.values())
    active = len([u for u in totals if u != "system"])
    print("-" * len(header))
    print(f"{active} user(s), about ${cost:.4f} in total" + (f", ${cost / active / args.days * 30:.4f} per user per 30 days" if active else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
