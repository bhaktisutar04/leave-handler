"""
agent.py — The MCP agent loop.

Key design: each email is processed in its own fresh conversation.
This keeps context short and prevents Groq from generating malformed tool calls.

Flow:
  1. Read all new emails directly (no Groq needed for this)
  2. For each email — mark as processed immediately, then run Groq conversation
  3. Post Slack summary only if emails were processed
"""

import json
import re
import time
from groq import Groq
from groq import BadRequestError
try:
    from groq import RateLimitError
except ImportError:
    from groq import APIStatusError as RateLimitError

from config import GROQ_API_KEY, SCAN_DAYS_BACK
from tool_schemas import MCP_TOOLS
from prompts import SYSTEM_PROMPT
from tools import execute_tool, save_processed_id

MAX_STEPS               = 20
MAX_BAD_REQUEST_RETRIES = 2
MAX_RATE_LIMIT_RETRIES  = 2

EMAIL_TOOLS = [t for t in MCP_TOOLS if t["function"]["name"] not in ("read_emails", "notify_slack")]

# Words in a draft body that signal a decline — used to block
# add_calendar_event being called after a decline draft is saved
DECLINE_SIGNALS = [
    "decline", "declined", "cannot approve", "unable to approve",
    "not able to approve", "team limit", "team is at capacity",
    "blackout", "insufficient notice", "not approved", "regret",
]


def run_agent() -> str:
    print("\n" + "=" * 55)
    print("  Leave Handler Agent — starting run")
    print("=" * 55)

    print("\n[Step 1] Reading new leave request emails...")
    email_result = execute_tool("read_emails", {"days_back": SCAN_DAYS_BACK})

    if email_result.get("error"):
        print(f"  ✗ Error reading emails: {email_result['error']}")
        return "Failed to read emails."

    emails  = email_result.get("emails", [])
    skipped = email_result.get("skipped", 0)
    print(f"  Found {len(emails)} new email(s), skipped {skipped} already processed")

    if not emails:
        print("  No new emails — nothing to do.")
        return "No new leave requests."

    results = []
    for i, email in enumerate(emails, 1):
        print(f"\n{'-' * 55}")
        print(f"  Processing email {i}/{len(emails)}: {email['subject']}")
        print(f"{'-' * 55}")
        outcome = _process_single_email(email)
        results.append(outcome)

        # TPD exhausted — stop, remaining emails retry next run
        if outcome.get("abort_run"):
            remaining = emails[i:]
            if remaining:
                print(f"\n  Aborting — {len(remaining)} email(s) will retry next run:")
                for e in remaining:
                    print(f"    • {e['subject']}")
            break

    print("\n[Final] Posting summary to Slack...")
    summary = _build_summary(results)
    execute_tool("notify_slack", {"message": summary})
    print("  Slack notified.")

    print("\n" + "=" * 55)
    print("  Agent finished")
    print("=" * 55)
    print(f"\nSummary:\n{summary}")
    return summary


def _process_single_email(email: dict) -> dict:
    """
    Processes one leave request email in a fresh Groq conversation.

    Safeguard: tracks whether a decline draft was saved, and blocks
    add_calendar_event from being called afterwards — even if Groq
    hallucinates the call after saving a decline draft.
    """
    print(f"  Marking email {email['email_id'][:8]}... as processed upfront")
    save_processed_id(email["email_id"])

    client = Groq(api_key=GROQ_API_KEY)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Process this single leave request email:\n\n"
                f"Email ID: {email['email_id']}\n"
                f"From: {email['sender']}\n"
                f"Subject: {email['subject']}\n"
                f"Date received: {email['date']}\n"
                f"Body:\n{email['body']}\n\n"
                f"Steps to follow:\n"
                f"1. Extract the leave dates from the email\n"
                f"2. Call check_calendar with those dates\n"
                f"3. Decide: approve or decline based on leave policy\n"
                f"4. Call save_draft with your reply (pass email_id: {email['email_id']})\n"
                f"   Keep the draft body concise — 3 to 5 sentences maximum.\n"
                f"5. If approved: call add_calendar_event\n"
                f"6. Your final line MUST start with exactly one word: APPROVED, DECLINED, or FLAGGED — then explain why"
            )
        },
    ]

    step                = 0
    bad_request_retries = 0
    rate_limit_retries  = 0
    draft_was_decline   = False   # tracks whether a decline draft was saved

    while step < MAX_STEPS:
        step += 1

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                tools=EMAIL_TOOLS,
                tool_choice="auto",
                max_tokens=1024,
            )
            bad_request_retries = 0

        except (RateLimitError, Exception) as e:
            error_str = str(e)

            is_rate_limit = (
                "429" in error_str
                or "rate_limit" in error_str.lower()
                or "rate limit" in error_str.lower()
                or (hasattr(e, "status_code") and getattr(e, "status_code", 0) == 429)
            )

            if is_rate_limit:
                is_tpd = "tokens per day" in error_str.lower() or "tpd" in error_str.lower()
                if is_tpd:
                    print(f"  ✗ Daily token quota (TPD) exhausted — aborting run.")
                    print(f"  ↩ Un-marking {email['email_id'][:8]}... so it retries next run")
                    _unmark_processed_id(email["email_id"])
                    return {
                        "email_id":  email["email_id"],
                        "sender":    email["sender"],
                        "subject":   email["subject"],
                        "outcome":   "ERROR",
                        "detail":    "Daily Groq token quota exhausted. Will retry next run.",
                        "abort_run": True,
                    }

                wait_seconds = _parse_retry_after(error_str)
                if rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                    rate_limit_retries += 1
                    print(f"  ⏳ Rate limit on step {step} — waiting {wait_seconds}s ({rate_limit_retries}/{MAX_RATE_LIMIT_RETRIES})...")
                    time.sleep(wait_seconds)
                    step -= 1
                    continue
                print(f"  ✗ Per-minute rate limit exceeded after {MAX_RATE_LIMIT_RETRIES} retries.")
                break

            if isinstance(e, BadRequestError) and "tool_use_failed" in error_str:
                if bad_request_retries < MAX_BAD_REQUEST_RETRIES:
                    bad_request_retries += 1
                    print(f"  ⚠ Groq malformed tool call on step {step} — retrying ({bad_request_retries}/{MAX_BAD_REQUEST_RETRIES})...")
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your last tool call was malformed and could not be parsed. "
                            "Please call the correct tool again using valid JSON arguments only. "
                            "Do not include any wrapper syntax, extra text, or preamble — "
                            "just invoke the tool directly with the correct parameters."
                        ),
                    })
                    step -= 1
                    continue
                print(f"  ⚠ Groq BadRequestError on step {step}: {e}")
                break

            print(f"  ✗ Unexpected error on step {step}: {e}")
            break

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append({
                "role":       "assistant",
                "content":    msg.content or "",
                "tool_calls": [
                    {
                        "id":       tc.id,
                        "type":     "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            if msg.content:
                print(f"\n  Groq: {msg.content[:200]}")

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                # ── Safeguard: detect if this draft is a decline ──
                if tool_name == "save_draft":
                    body_lower = tool_args.get("body", "").lower()
                    if any(signal in body_lower for signal in DECLINE_SIGNALS):
                        draft_was_decline = True
                        print(f"  ℹ Draft body contains decline language — blocking any future add_calendar_event call")

                # ── Safeguard: block add_calendar_event after a decline draft ──
                if tool_name == "add_calendar_event" and draft_was_decline:
                    print(f"  🚫 BLOCKED add_calendar_event — a decline draft was already saved for this email")
                    fake_result = {
                        "success": False,
                        "error": "Blocked: cannot add calendar event after a decline draft was saved.",
                    }
                    _print_result(tool_name, fake_result)
                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc.id,
                        "content":      json.dumps(fake_result),
                    })
                    continue

                print(f"\n  -> {tool_name}({json.dumps(tool_args)})")
                tool_result = execute_tool(tool_name, tool_args)
                _print_result(tool_name, tool_result)

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      json.dumps(tool_result),
                })

        else:
            final = msg.content or ""
            print(f"\n  Groq outcome: {final[:200]}")
            outcome = _parse_outcome(final)
            return {
                "email_id": email["email_id"],
                "sender":   email["sender"],
                "subject":  email["subject"],
                "outcome":  outcome,
                "detail":   final[:300],
            }

    return {
        "email_id": email["email_id"],
        "sender":   email["sender"],
        "subject":  email["subject"],
        "outcome":  "ERROR",
        "detail":   "Processing failed — see agent log for details.",
    }


def _parse_retry_after(error_str: str) -> int:
    phrase = re.search(r'try again in (.+?)(?:\.|$)', error_str, re.IGNORECASE)
    if phrase:
        chunk = phrase.group(1)
        m = re.search(r'(\d+)m(\d+(?:\.\d+)?)s', chunk)
        if m:
            return int(int(m.group(1)) * 60 + float(m.group(2))) + 5
        m = re.search(r'(\d+(?:\.\d+)?)s', chunk)
        if m:
            return int(float(m.group(1))) + 5
    return 65


def _unmark_processed_id(email_id: str) -> None:
    from tools import LOG_FILE
    import os
    if not os.path.exists(LOG_FILE):
        return
    try:
        with open(LOG_FILE) as f:
            ids = set(json.load(f))
        ids.discard(email_id)
        with open(LOG_FILE, "w") as f:
            json.dump(sorted(list(ids)), f, indent=2)
        print(f"  ✓ Removed {email_id[:8]}... from processed log")
    except Exception as e:
        print(f"  ⚠ Could not un-mark email: {e}")


def _parse_outcome(final: str) -> str:
    for line in final.strip().splitlines():
        stripped = line.strip().upper()
        if not stripped:
            continue
        if stripped.startswith("APPROVED"):
            return "APPROVED"
        if stripped.startswith("DECLINED"):
            return "DECLINED"
        if stripped.startswith("FLAGGED"):
            return "FLAGGED"
        break
    for line in final.strip().splitlines():
        word = line.strip().split()[0].upper().rstrip(":.!,") if line.strip() else ""
        if word == "APPROVED":
            return "APPROVED"
        if word == "DECLINED":
            return "DECLINED"
        if word == "FLAGGED":
            return "FLAGGED"
    print("  ⚠ Could not determine outcome — marking as FLAGGED")
    return "FLAGGED"


def _build_summary(results: list) -> str:
    display  = [r for r in results if not r.get("abort_run")]
    total    = len(display)
    approved = [r for r in display if r["outcome"] == "APPROVED"]
    declined = [r for r in display if r["outcome"] == "DECLINED"]
    flagged  = [r for r in display if r["outcome"] == "FLAGGED"]
    errors   = [r for r in display if r["outcome"] == "ERROR"]

    lines = [f"*Leave Request Summary — {total} request(s) processed*"]

    if approved:
        lines.append(f"\n✅ Approved ({len(approved)}):")
        for r in approved:
            lines.append(f"  • {r['sender']} — {r['subject']}")
    if declined:
        lines.append(f"\n❌ Declined ({len(declined)}):")
        for r in declined:
            lines.append(f"  • {r['sender']} — {r['subject']}")
    if flagged:
        lines.append(f"\n⚠️ Needs review ({len(flagged)}):")
        for r in flagged:
            lines.append(f"  • {r['sender']} — {r['subject']}")
    if errors:
        lines.append(f"\n🔴 Errors — manual review needed ({len(errors)}):")
        for r in errors:
            lines.append(f"  • {r['sender']} — {r['subject']}")
            lines.append(f"    ↳ {r['detail']}")

    aborted = [r for r in results if r.get("abort_run")]
    if aborted:
        lines.append(f"\n⏹ Run aborted — daily Groq token quota exhausted.")
        lines.append(f"  Remaining emails will retry on next scheduled run.")

    return "\n".join(lines)


def _print_result(tool_name: str, result: dict) -> None:
    if result.get("error"):
        print(f"    ✗ ERROR: {result['error']}")
        return
    if tool_name == "check_calendar":
        people    = result.get("people_on_leave", 0)
        max_p     = result.get("max_people_on_leave", 2)
        limited   = result.get("team_limit_reached", False)
        blackout  = result.get("is_blackout_date", False)
        conflicts = result.get("conflict_count", 0)
        print(f"    ✓ {people}/{max_p} on leave — limit: {limited} — blackout: {blackout} — conflicts: {conflicts}")
    elif tool_name == "save_draft":
        print(f"    ✓ Draft saved -> {result.get('to')} | {result.get('subject')}")
    elif tool_name == "add_calendar_event":
        print(f"    ✓ Calendar event added — {result.get('event_title')} ({result.get('date_range')})")
    elif tool_name == "notify_slack":
        print(f"    ✓ Slack sent")
    else:
        print(f"    ✓ {json.dumps(result)[:100]}")