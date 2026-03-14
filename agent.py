"""
agent.py — The MCP agent loop.

Key design: each email is processed in its own fresh conversation.
This keeps context short and prevents Groq from generating malformed tool calls.

Flow:
  1. One Groq call to read all emails
  2. For each email — fresh conversation → check calendar → save draft → add event
  3. One final Groq call to post Slack summary
"""

import json
from groq import Groq, BadRequestError
from config import GROQ_API_KEY
from tool_schemas import MCP_TOOLS
from prompts import SYSTEM_PROMPT
from tools import execute_tool

MAX_STEPS = 20


def run_agent() -> str:
    print("\n" + "═" * 55)
    print("  Leave Handler Agent — starting run")
    print("═" * 55)

    # ── Step 1: Read all new emails ──
    print("\n[Step 1] Reading new leave request emails...")
    email_result = execute_tool("read_emails", {"days_back": 7})

    if email_result.get("error"):
        print(f"  ✗ Error reading emails: {email_result['error']}")
        return "Failed to read emails."

    emails  = email_result.get("emails", [])
    skipped = email_result.get("skipped", 0)
    print(f"  Found {len(emails)} new email(s), skipped {skipped} already processed")

    if not emails:
        msg = "No new leave requests this week."
        execute_tool("notify_slack", {"message": msg})
        print(f"  Notified Slack: {msg}")
        return msg

    # ── Step 2: Process each email in its own fresh conversation ──
    results = []
    for i, email in enumerate(emails, 1):
        print(f"\n{'─'*55}")
        print(f"  Processing email {i}/{len(emails)}: {email['subject']}")
        print(f"{'─'*55}")
        outcome = _process_single_email(email)
        results.append(outcome)

    # ── Step 3: Post Slack summary ──
    print(f"\n[Final] Posting summary to Slack...")
    summary = _build_summary(results)
    execute_tool("notify_slack", {"message": summary})
    print(f"  Slack notified.")

    print("\n" + "═" * 55)
    print("  Agent finished")
    print("═" * 55)
    print(f"\nSummary:\n{summary}")
    return summary


def _process_single_email(email: dict) -> dict:
    """
    Processes one leave request email in a fresh Groq conversation.
    Returns a dict describing what happened (approved/declined/flagged).
    """
    client = Groq(api_key=GROQ_API_KEY)

    # Fresh conversation for each email — keeps context short
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
                f"5. If approved: call add_calendar_event\n"
                f"6. Reply with one line: APPROVED, DECLINED, or FLAGGED and why"
            )
        },
    ]

    step = 0
    while step < MAX_STEPS:
        step += 1

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                tools=MCP_TOOLS,
                tool_choice="auto",
                max_tokens=4096,
            )
        except BadRequestError as e:
            print(f"  ⚠ Groq error on step {step}: {e}")
            break
        except Exception as e:
            print(f"  ✗ Unexpected error: {e}")
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

                print(f"\n  → {tool_name}({json.dumps(tool_args)})")
                tool_result = execute_tool(tool_name, tool_args)
                _print_result(tool_name, tool_result)

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      json.dumps(tool_result),
                })

        else:
            # Groq finished — extract outcome
            final = msg.content or ""
            print(f"\n  Groq outcome: {final[:200]}")

            outcome = "APPROVED" if "APPROVED" in final.upper() else \
                      "DECLINED" if "DECLINED" in final.upper() else "FLAGGED"

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
        "detail":   "Processing failed due to repeated Groq errors.",
    }


def _build_summary(results: list) -> str:
    """Builds a concise Slack summary from all processed emails."""
    total    = len(results)
    approved = [r for r in results if r["outcome"] == "APPROVED"]
    declined = [r for r in results if r["outcome"] == "DECLINED"]
    flagged  = [r for r in results if r["outcome"] == "FLAGGED"]
    errors   = [r for r in results if r["outcome"] == "ERROR"]

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
        lines.append(f"\n🔴 Errors ({len(errors)}):")
        for r in errors:
            lines.append(f"  • {r['sender']} — {r['subject']}")

    return "\n".join(lines)


def _print_result(tool_name: str, result: dict) -> None:
    if result.get("error"):
        print(f"    ✗ ERROR: {result['error']}")
        return
    if tool_name == "check_calendar":
        people  = result.get("people_on_leave", 0)
        max_p   = result.get("max_people_on_leave", 2)
        limited = result.get("team_limit_reached", False)
        print(f"    ✓ {people}/{max_p} people on leave — limit reached: {limited}")
    elif tool_name == "save_draft":
        print(f"    ✓ Draft saved → {result.get('to')} | {result.get('subject')}")
    elif tool_name == "add_calendar_event":
        print(f"    ✓ Calendar event added — {result.get('event_title')} ({result.get('date_range')})")
    elif tool_name == "notify_slack":
        print(f"    ✓ Slack sent")
    else:
        print(f"    ✓ {json.dumps(result)[:100]}")