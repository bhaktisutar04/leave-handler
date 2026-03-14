"""
test_tools.py — Run this on Day 3 to verify each tool works before building the agent.

Usage:
    python test_tools.py slack       ← test Slack notification
    python test_tools.py emails      ← test Gmail scanning
    python test_tools.py calendar    ← test Calendar check
    python test_tools.py draft       ← test saving a Gmail draft
    python test_tools.py all         ← test everything in order
"""

import sys
from datetime import datetime, timedelta

def test_slack():
    print("\n── Test 1: notify_slack ──")
    from tools import notify_slack
    result = notify_slack("Test from Leave Handler — Day 3 setup working!")
    print("Result:", result)
    if result["success"]:
        print("✓ PASSED — check your #leave-alerts Slack channel")
    else:
        print("✗ FAILED —", result["error"])

def test_emails():
    print("\n── Test 2: read_emails ──")
    from tools import read_emails
    result = read_emails(days_back=7)
    print(f"Found {result['count']} leave request emails")
    if result["error"]:
        print("✗ FAILED —", result["error"])
    else:
        print("✓ PASSED")
        for e in result["emails"]:
            print(f"  → From: {e['sender']}  |  Subject: {e['subject']}")

def test_calendar():
    print("\n── Test 3: check_calendar ──")
    from tools import check_calendar
    today     = datetime.now().strftime("%Y-%m-%d")
    next_week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    result    = check_calendar(today, next_week)
    print(f"Events found: {result['event_count']}  |  Has conflict: {result['has_conflict']}")
    if result["error"]:
        print("✗ FAILED —", result["error"])
    else:
        print("✓ PASSED")
        for ev in result["events"]:
            print(f"  → {ev['title']}  ({ev['start']})")

def test_draft():
    print("\n── Test 4: save_draft ──")
    from tools import save_draft
    import os
    # Gets your own Gmail to send the test draft to yourself
    from auth import get_google_services
    gmail, _ = get_google_services()
    profile  = gmail.users().getProfile(userId="me").execute()
    my_email = profile["emailAddress"]

    result = save_draft(
        to_email=my_email,
        subject="[TEST] Leave Request Draft — ignore this",
        body="This is a test draft created by leave_handler/test_tools.py on Day 3.\n\nYou can delete this."
    )
    print("Result:", result)
    if result["success"]:
        print("✓ PASSED — check your Gmail Drafts folder")
    else:
        print("✗ FAILED —", result["error"])


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"

    if arg == "slack":
        test_slack()
    elif arg == "emails":
        test_emails()
    elif arg == "calendar":
        test_calendar()
    elif arg == "draft":
        test_draft()
    elif arg == "all":
        test_slack()
        test_emails()
        test_calendar()
        test_draft()
        print("\n── All tests complete ──")
    else:
        print("Usage: python test_tools.py [slack|emails|calendar|draft|all]")