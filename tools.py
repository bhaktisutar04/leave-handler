"""
tools.py — The 5 MCP tool functions for the Leave Handler agent.

Tools:
  1. read_emails(days_back)                    → scan Gmail for NEW leave requests only
  2. check_calendar(start, end)                → check conflicts + count people already on leave
  3. save_draft(to, subject, body, email_id)   → save reply draft to Gmail Drafts
  4. add_calendar_event(name, start, end)      → add approved leave to Google Calendar
  5. notify_slack(message)                     → post summary to Slack channel

FIXES:
  - check_calendar: fixed timeMax boundary for all-day events (was T23:59:59Z, now uses
    next day T00:00:00Z so Google Calendar all-day events are correctly included)
  - check_calendar: blackout dates now enforced at the tool layer — returns is_blackout_date=True
    so Groq sees it as a hard signal rather than relying on prompt memory alone
"""

import base64
import json
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Any

import requests
from googleapiclient.errors import HttpError

from auth import get_google_services
from config import LEAVE_KEYWORDS, SCAN_DAYS_BACK, SLACK_WEBHOOK_URL, LEAVE_POLICY

# ─────────────────────────────────────────────
# Leave tag — used to identify leave events in calendar
# ─────────────────────────────────────────────

LEAVE_TAG = "[LEAVE]"   # All approved leave events are prefixed with this


# ─────────────────────────────────────────────
# Processed email log — prevents re-processing
# ─────────────────────────────────────────────

LOG_FILE = "processed_emails.json"


def _load_processed_ids() -> set:
    """
    Loads the set of already-processed email IDs.
    Creates empty file if it doesn't exist.
    """
    if not os.path.exists(LOG_FILE):
        print(f"  Creating new {LOG_FILE} (first run)")
        with open(LOG_FILE, "w") as f:
            json.dump([], f)
        return set()

    try:
        with open(LOG_FILE) as f:
            ids = json.load(f)
            if not isinstance(ids, list):
                print(f"  ⚠ {LOG_FILE} is corrupted (not a list), resetting")
                return set()
            print(f"  Loaded {len(ids)} processed email IDs from {LOG_FILE}")
            return set(ids)
    except json.JSONDecodeError:
        print(f"  ⚠ {LOG_FILE} is corrupted (invalid JSON), resetting")
        with open(LOG_FILE, "w") as f:
            json.dump([], f)
        return set()
    except Exception as e:
        print(f"  ⚠ Error loading {LOG_FILE}: {e}")
        return set()


def _save_processed_id(email_id: str) -> None:
    """
    Adds an email ID to the processed list.
    """
    ids = _load_processed_ids()

    if email_id in ids:
        print(f"  (Email {email_id[:8]}... already in log)")
        return

    ids.add(email_id)

    try:
        with open(LOG_FILE, "w") as f:
            json.dump(sorted(list(ids)), f, indent=2)
        print(f"  ✓ Email {email_id[:8]}... marked as processed ({len(ids)} total)")
    except Exception as e:
        print(f"  ✗ Failed to save processed email ID: {e}")


# ─────────────────────────────────────────────
# Shared helper — get authenticated services once
# ─────────────────────────────────────────────

_gmail    = None
_calendar = None


def _get_services():
    """
    Returns cached Gmail and Calendar service objects.
    Only authenticates once per agent run.
    """
    global _gmail, _calendar
    if _gmail is None or _calendar is None:
        print("\n[Authenticating with Google...]")
        _gmail, _calendar = get_google_services()
    return _gmail, _calendar


# ─────────────────────────────────────────────
# Tool 1 — read_emails
# ─────────────────────────────────────────────

def read_emails(days_back: int = SCAN_DAYS_BACK) -> dict[str, Any]:
    """
    Searches Gmail for NEW leave request emails only.
    Already-processed emails are automatically skipped.
    """
    try:
        gmail, _      = _get_services()
        processed_ids = _load_processed_ids()

        since_date    = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
        keyword_query = " OR ".join([f'"{kw}"' for kw in LEAVE_KEYWORDS])
        query         = f"({keyword_query}) after:{since_date} in:inbox"

        print(f"  Gmail query: {query}")
        result   = gmail.users().messages().list(userId="me", q=query, maxResults=20).execute()
        messages = result.get("messages", [])

        if not messages:
            print("  No emails found matching search criteria")
            return {"emails": [], "count": 0, "skipped": 0, "error": None}

        print(f"  Found {len(messages)} email(s) from Gmail")
        emails  = []
        skipped = 0

        for msg in messages:
            msg_id = msg["id"]

            if msg_id in processed_ids:
                skipped += 1
                print(f"  ⊘ Skipping {msg_id[:8]}... (already processed)")
                continue

            try:
                detail  = gmail.users().messages().get(userId="me", id=msg_id, format="full").execute()
                headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
                body    = _extract_body(detail["payload"])

                email_data = {
                    "email_id": msg_id,
                    "sender":   headers.get("From", "Unknown"),
                    "subject":  headers.get("Subject", "(no subject)"),
                    "date":     headers.get("Date", "Unknown date"),
                    "body":     body[:2000],  # Truncate to 2000 chars
                }

                emails.append(email_data)
                print(f"  ✓ Loaded {msg_id[:8]}... | From: {email_data['sender'][:30]} | Subject: {email_data['subject'][:40]}")

            except Exception as e:
                print(f"  ✗ Failed to load email {msg_id[:8]}...: {e}")
                continue

        return {"emails": emails, "count": len(emails), "skipped": skipped, "error": None}

    except HttpError as e:
        error_msg = f"Gmail API error: {e}"
        print(f"  ✗ {error_msg}")
        return {"emails": [], "count": 0, "skipped": 0, "error": error_msg}
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"  ✗ {error_msg}")
        return {"emails": [], "count": 0, "skipped": 0, "error": error_msg}


def _extract_body(payload: dict) -> str:
    """Recursively extracts text body from email payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    if "parts" in payload:
        for part in payload["parts"]:
            text = _extract_body(part)
            if text:
                return text
    return "(no readable body)"


# ─────────────────────────────────────────────
# Tool 2 — check_calendar
# ─────────────────────────────────────────────

def _is_blackout(start_date: str, end_date: str) -> tuple[bool, list[str]]:
    """
    Checks whether any date in the requested range falls on a company blackout date.

    Returns:
        (is_blackout, list_of_matching_blackout_dates)
    """
    blackout_dates = LEAVE_POLICY.get("blackout_dates", [])
    if not blackout_dates:
        return False, []

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date,   "%Y-%m-%d")
    except ValueError:
        return False, []

    hits = []
    for bd in blackout_dates:
        try:
            bd_dt = datetime.strptime(bd, "%Y-%m-%d")
            if start_dt <= bd_dt <= end_dt:
                hits.append(bd)
        except ValueError:
            continue

    return len(hits) > 0, hits


def check_calendar(start_date: str, end_date: str) -> dict[str, Any]:
    """
    Checks Google Calendar for the requested leave dates. Does three things:
      1. Finds any existing events (conflicts).
      2. Counts how many team members are already on approved leave those days
         by looking for [LEAVE] events.
      3. Checks if any date in the range is a company blackout date (from config).

    If team_limit_reached is True or is_blackout_date is True, Groq MUST decline.

    FIX: timeMax now uses the day AFTER end_date at 00:00:00Z (instead of
    end_date T23:59:59Z) so Google Calendar all-day events — which store their
    end as the exclusive next day — are correctly included in query results.

    Args:
        start_date: YYYY-MM-DD
        end_date:   YYYY-MM-DD
    """
    # ── Blackout check (no API call needed) ──
    blackout_hit, blackout_matches = _is_blackout(start_date, end_date)

    try:
        _, calendar = _get_services()

        # FIX: For all-day events, Google Calendar stores end date as exclusive
        # (the day after the last day). Using end_date T23:59:59Z misses events
        # whose end field is set to the next day at 00:00:00Z.
        # Correct approach: set timeMax to the day after end_date at 00:00:00Z.
        try:
            time_max_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            time_max    = f"{time_max_dt.strftime('%Y-%m-%d')}T00:00:00Z"
        except ValueError:
            # Fallback if date parsing fails
            time_max = f"{end_date}T23:59:59Z"

        result = calendar.events().list(
            calendarId="primary",
            timeMin=f"{start_date}T00:00:00Z",
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        items              = result.get("items", [])
        leave_events       = []   # [LEAVE] tagged events — used to count team members off
        non_leave_events   = []   # all other events — real scheduling conflicts

        for item in items:
            title = item.get("summary", "(untitled)")
            start = item.get("start", {}).get("date") or item.get("start", {}).get("dateTime", "")
            end   = item.get("end",   {}).get("date") or item.get("end",   {}).get("dateTime", "")
            entry = {"title": title, "start": start, "end": end}

            if title.startswith(LEAVE_TAG):
                # This is another team member's approved leave — only used for headcount
                leave_events.append(entry)
            else:
                # This is a real calendar event (meeting, holiday, deadline, etc.)
                non_leave_events.append(entry)

        max_allowed        = LEAVE_POLICY.get("max_people_on_leave", 2)
        people_on_leave    = len(leave_events)
        team_limit_reached = people_on_leave >= max_allowed

        # has_conflict refers ONLY to non-leave events.
        # [LEAVE] events for other team members are NOT a conflict reason —
        # they are headcount only. Groq must NOT decline because of them.
        has_real_conflict  = len(non_leave_events) > 0

        try:
            s          = datetime.strptime(start_date, "%Y-%m-%d")
            e          = datetime.strptime(end_date,   "%Y-%m-%d")
            date_range = f"{s.strftime('%b %d')} - {e.strftime('%b %d, %Y')}"
        except ValueError:
            date_range = f"{start_date} to {end_date}"

        if blackout_hit:
            print(f"    ⚠ Blackout date(s) detected: {blackout_matches}")

        return {
            # Leave headcount — use these to check team_limit_reached only
            "leave_events":         leave_events,
            "people_on_leave":      people_on_leave,
            "max_people_on_leave":  max_allowed,
            "team_limit_reached":   team_limit_reached,

            # Real scheduling conflicts (meetings, holidays, etc.) — NOT other people's leave
            # Only use has_conflict to flag for manual review, NOT to auto-decline
            "non_leave_events":     non_leave_events,
            "has_conflict":         has_real_conflict,
            "conflict_count":       len(non_leave_events),

            "date_range":           date_range,
            # Blackout fields — enforced here so Groq sees them as hard signals
            "is_blackout_date":     blackout_hit,
            "blackout_dates_hit":   blackout_matches,
            "error":                None,
        }

    except HttpError as e:
        error_msg = f"Calendar API error: {e}"
        print(f"  ✗ {error_msg}")
        return {
            "leave_events": [], "people_on_leave": 0,
            "max_people_on_leave": 2, "team_limit_reached": False,
            "non_leave_events": [], "has_conflict": False, "conflict_count": 0,
            "date_range": "",
            "is_blackout_date": blackout_hit, "blackout_dates_hit": blackout_matches,
            "error": error_msg,
        }
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"  ✗ {error_msg}")
        return {
            "leave_events": [], "people_on_leave": 0,
            "max_people_on_leave": 2, "team_limit_reached": False,
            "non_leave_events": [], "has_conflict": False, "conflict_count": 0,
            "date_range": "",
            "is_blackout_date": blackout_hit, "blackout_dates_hit": blackout_matches,
            "error": error_msg,
        }


# ─────────────────────────────────────────────
# Tool 3 — save_draft
# ─────────────────────────────────────────────

def save_draft(to_email: str, subject: str, body: str, email_id: str = "") -> dict[str, Any]:
    """
    Saves an email reply as a Gmail Draft.
    Also marks the original email as processed so it won't be picked up again.
    """
    try:
        gmail, _ = _get_services()

        message            = MIMEText(body)
        message["to"]      = to_email
        message["subject"] = subject
        raw                = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        draft = gmail.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()

        # Mark email as processed only after draft is successfully created
        if email_id:
            _save_processed_id(email_id)

        return {
            "success":  True,
            "draft_id": draft.get("id", ""),
            "to":       to_email,
            "subject":  subject,
            "error":    None,
        }

    except HttpError as e:
        error_msg = f"Gmail API error: {e}"
        print(f"  ✗ {error_msg}")
        return {
            "success":  False,
            "draft_id": "",
            "to":       to_email,
            "subject":  subject,
            "error":    error_msg,
        }
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"  ✗ {error_msg}")
        return {
            "success":  False,
            "draft_id": "",
            "to":       to_email,
            "subject":  subject,
            "error":    error_msg,
        }


# ─────────────────────────────────────────────
# Tool 4 — add_calendar_event
# ─────────────────────────────────────────────

def add_calendar_event(employee_name: str, start_date: str, end_date: str) -> dict[str, Any]:
    """
    Adds an approved leave entry to Google Calendar.
    Only call this AFTER deciding to approve — never for declines.

    The event is titled "[LEAVE] <employee_name>" so check_calendar can
    count how many people are on leave on any given day.

    Args:
        employee_name: Full name or email of the employee (e.g. "Bhakti Sutar")
        start_date:    Leave start date in YYYY-MM-DD format
        end_date:      Leave end date in YYYY-MM-DD format
    """
    try:
        _, calendar = _get_services()

        # Google Calendar all-day events: end date is exclusive (day after last leave day)
        end_dt        = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_exclusive = end_dt.strftime("%Y-%m-%d")

        event = {
            "summary":     f"{LEAVE_TAG} {employee_name}",
            "description": f"Approved leave for {employee_name}. Added automatically by Leave Handler.",
            "start":       {"date": start_date},
            "end":         {"date": end_exclusive},
            "colorId":     "6",   # Tangerine colour — easy to spot in calendar
        }

        created = calendar.events().insert(calendarId="primary", body=event).execute()

        # Human-readable date range for confirmation message
        try:
            s          = datetime.strptime(start_date, "%Y-%m-%d")
            e          = datetime.strptime(end_date,   "%Y-%m-%d")
            date_range = f"{s.strftime('%b %d')} - {e.strftime('%b %d, %Y')}"
        except ValueError:
            date_range = f"{start_date} to {end_date}"

        return {
            "success":       True,
            "event_id":      created.get("id", ""),
            "event_title":   created.get("summary", ""),
            "date_range":    date_range,
            "calendar_link": created.get("htmlLink", ""),
            "error":         None,
        }

    except HttpError as e:
        error_msg = f"Calendar API error: {e}"
        print(f"  ✗ {error_msg}")
        return {
            "success":       False,
            "event_id":      "",
            "event_title":   "",
            "date_range":    "",
            "calendar_link": "",
            "error":         error_msg,
        }
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"  ✗ {error_msg}")
        return {
            "success":       False,
            "event_id":      "",
            "event_title":   "",
            "date_range":    "",
            "calendar_link": "",
            "error":         error_msg,
        }


# ─────────────────────────────────────────────
# Tool 5 — notify_slack
# ─────────────────────────────────────────────

def notify_slack(message: str) -> dict[str, Any]:
    """Posts a message to the #leave-alerts Slack channel."""
    try:
        if not SLACK_WEBHOOK_URL:
            error_msg = "SLACK_WEBHOOK_URL not set in .env"
            print(f"  ⚠ {error_msg}")
            return {"success": False, "status": 0, "error": error_msg}

        response = requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=10)
        response.raise_for_status()

        return {"success": True, "status": response.status_code, "error": None}

    except requests.exceptions.Timeout:
        error_msg = "Slack request timed out"
        print(f"  ✗ {error_msg}")
        return {"success": False, "status": 0, "error": error_msg}
    except requests.exceptions.HTTPError as e:
        error_msg = f"Slack HTTP error: {e}"
        print(f"  ✗ {error_msg}")
        return {"success": False, "status": e.response.status_code, "error": error_msg}
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"  ✗ {error_msg}")
        return {"success": False, "status": 0, "error": error_msg}


# ─────────────────────────────────────────────
# Tool dispatcher — used by agent.py
# ─────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> dict:
    """Routes a tool call from Groq to the correct Python function."""
    tools = {
        "read_emails":        read_emails,
        "check_calendar":     check_calendar,
        "save_draft":         save_draft,
        "add_calendar_event": add_calendar_event,
        "notify_slack":       notify_slack,
    }
    if name not in tools:
        return {"error": f"Unknown tool: '{name}'. Available: {list(tools.keys())}"}
    try:
        return tools[name](**args)
    except TypeError as e:
        return {"error": f"Wrong arguments for tool '{name}': {e}"}