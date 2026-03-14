"""
tools.py — The 5 MCP tool functions for the Leave Handler agent.

Tools:
  1. read_emails(days_back)                    → scan Gmail for NEW leave requests only
  2. check_calendar(start, end)                → check conflicts + count people already on leave
  3. save_draft(to, subject, body, email_id)   → save reply draft to Gmail Drafts
  4. add_calendar_event(name, start, end)      → add approved leave to Google Calendar
  5. notify_slack(message)                     → post summary to Slack channel
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
    if not os.path.exists(LOG_FILE):
        return set()
    try:
        with open(LOG_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_processed_id(email_id: str) -> None:
    ids = _load_processed_ids()
    ids.add(email_id)
    with open(LOG_FILE, "w") as f:
        json.dump(list(ids), f, indent=2)


# ─────────────────────────────────────────────
# Shared helper — get authenticated services once
# ─────────────────────────────────────────────

_gmail    = None
_calendar = None

def _get_services():
    global _gmail, _calendar
    if _gmail is None or _calendar is None:
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

        result   = gmail.users().messages().list(userId="me", q=query, maxResults=20).execute()
        messages = result.get("messages", [])

        if not messages:
            return {"emails": [], "count": 0, "skipped": 0, "error": None}

        emails  = []
        skipped = 0

        for msg in messages:
            if msg["id"] in processed_ids:
                skipped += 1
                continue

            detail  = gmail.users().messages().get(userId="me", id=msg["id"], format="full").execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            body    = _extract_body(detail["payload"])

            emails.append({
                "email_id": msg["id"],
                "sender":   headers.get("From", "Unknown"),
                "subject":  headers.get("Subject", "(no subject)"),
                "date":     headers.get("Date", "Unknown date"),
                "body":     body[:2000],
            })

        return {"emails": emails, "count": len(emails), "skipped": skipped, "error": None}

    except HttpError as e:
        return {"emails": [], "count": 0, "skipped": 0, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"emails": [], "count": 0, "skipped": 0, "error": f"Unexpected error: {e}"}


def _extract_body(payload: dict) -> str:
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

def check_calendar(start_date: str, end_date: str) -> dict[str, Any]:
    """
    Checks Google Calendar for two things:
      1. General scheduling conflicts (any events on those dates)
      2. How many team members are already on approved leave those days
         (looks for events prefixed with [LEAVE])

    If people_on_leave >= max_people_on_leave (from config), sets team_limit_reached = True.
    Groq should decline the request if team_limit_reached is True.

    Args:
        start_date: YYYY-MM-DD
        end_date:   YYYY-MM-DD
    """
    try:
        _, calendar = _get_services()

        result = calendar.events().list(
            calendarId="primary",
            timeMin=f"{start_date}T00:00:00Z",
            timeMax=f"{end_date}T23:59:59Z",
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        items  = result.get("items", [])
        all_events   = []
        leave_events = []   # only [LEAVE] tagged events

        for item in items:
            title = item.get("summary", "(untitled)")
            start = item.get("start", {}).get("date") or item.get("start", {}).get("dateTime", "")
            end   = item.get("end",   {}).get("date") or item.get("end",   {}).get("dateTime", "")

            all_events.append({"title": title, "start": start, "end": end})

            if title.startswith(LEAVE_TAG):
                leave_events.append({"title": title, "start": start, "end": end})

        max_allowed      = LEAVE_POLICY.get("max_people_on_leave", 2)
        people_on_leave  = len(leave_events)
        team_limit_reached = people_on_leave >= max_allowed

        try:
            s          = datetime.strptime(start_date, "%Y-%m-%d")
            e          = datetime.strptime(end_date,   "%Y-%m-%d")
            date_range = f"{s.strftime('%b %d')} - {e.strftime('%b %d, %Y')}"
        except ValueError:
            date_range = f"{start_date} to {end_date}"

        return {
            "all_events":          all_events,
            "leave_events":        leave_events,
            "people_on_leave":     people_on_leave,
            "max_people_on_leave": max_allowed,
            "team_limit_reached":  team_limit_reached,
            "has_conflict":        len(all_events) > 0,
            "event_count":         len(all_events),
            "date_range":          date_range,
            "error":               None,
        }

    except HttpError as e:
        return {"all_events": [], "leave_events": [], "people_on_leave": 0,
                "max_people_on_leave": 2, "team_limit_reached": False,
                "has_conflict": False, "event_count": 0, "date_range": "", "error": f"Calendar API error: {e}"}
    except Exception as e:
        return {"all_events": [], "leave_events": [], "people_on_leave": 0,
                "max_people_on_leave": 2, "team_limit_reached": False,
                "has_conflict": False, "event_count": 0, "date_range": "", "error": f"Unexpected error: {e}"}


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

        if email_id:
            _save_processed_id(email_id)

        return {"success": True, "draft_id": draft.get("id", ""), "to": to_email, "subject": subject, "error": None}

    except HttpError as e:
        return {"success": False, "draft_id": "", "to": to_email, "subject": subject, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "draft_id": "", "to": to_email, "subject": subject, "error": f"Unexpected error: {e}"}


# ─────────────────────────────────────────────
# Tool 4 — add_calendar_event  (NEW)
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
                       (for Google Calendar all-day events, end_date should be
                        the day AFTER the last day of leave)
    """
    try:
        _, calendar = _get_services()

        # Google Calendar all-day events: end date is exclusive (day after last leave day)
        end_dt       = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
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
        return {"success": False, "event_id": "", "event_title": "", "date_range": "", "calendar_link": "", "error": f"Calendar API error: {e}"}
    except Exception as e:
        return {"success": False, "event_id": "", "event_title": "", "date_range": "", "calendar_link": "", "error": f"Unexpected error: {e}"}


# ─────────────────────────────────────────────
# Tool 5 — notify_slack
# ─────────────────────────────────────────────

def notify_slack(message: str) -> dict[str, Any]:
    """Posts a message to the #leave-alerts Slack channel."""
    try:
        if not SLACK_WEBHOOK_URL:
            return {"success": False, "status": 0, "error": "SLACK_WEBHOOK_URL not set in .env"}
        response = requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=10)
        response.raise_for_status()
        return {"success": True, "status": response.status_code, "error": None}

    except requests.exceptions.Timeout:
        return {"success": False, "status": 0, "error": "Slack request timed out"}
    except requests.exceptions.HTTPError as e:
        return {"success": False, "status": e.response.status_code, "error": f"Slack HTTP error: {e}"}
    except Exception as e:
        return {"success": False, "status": 0, "error": f"Unexpected error: {e}"}


# ─────────────────────────────────────────────
# Tool dispatcher — used by agent.py
# ─────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> dict:
    """Routes a tool call from Groq to the correct Python function."""
    tools = {
        "read_emails":       read_emails,
        "check_calendar":    check_calendar,
        "save_draft":        save_draft,
        "add_calendar_event": add_calendar_event,
        "notify_slack":      notify_slack,
    }
    if name not in tools:
        return {"error": f"Unknown tool: '{name}'. Available: {list(tools.keys())}"}
    try:
        return tools[name](**args)
    except TypeError as e:
        return {"error": f"Wrong arguments for tool '{name}': {e}"}