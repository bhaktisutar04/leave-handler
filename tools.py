"""
tools.py — The 5 MCP tool functions for the Leave Handler agent.
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

LEAVE_TAG        = "[LEAVE]"
LOG_FILE         = "processed_emails.json"
MAX_LOG_ENTRIES  = 500   # cap so the file doesn't grow forever over months of runs


# ─────────────────────────────────────────────
# Processed email log
# ─────────────────────────────────────────────

def _load_processed_ids() -> set:
    if not os.path.exists(LOG_FILE):
        print(f"  Creating new {LOG_FILE} (first run)")
        with open(LOG_FILE, "w") as f:
            json.dump([], f)
        return set()
    try:
        with open(LOG_FILE) as f:
            ids = json.load(f)
            if not isinstance(ids, list):
                print(f"  ⚠ {LOG_FILE} corrupted (not a list), resetting")
                return set()
            print(f"  Loaded {len(ids)} processed email IDs from {LOG_FILE}")
            return set(ids)
    except json.JSONDecodeError:
        print(f"  ⚠ {LOG_FILE} corrupted (invalid JSON), resetting")
        with open(LOG_FILE, "w") as f:
            json.dump([], f)
        return set()
    except Exception as e:
        print(f"  ⚠ Error loading {LOG_FILE}: {e}")
        return set()


def _write_processed_id(email_id: str) -> None:
    """Adds one email_id to the log. Caps the log at MAX_LOG_ENTRIES."""
    ids = _load_processed_ids()
    if email_id in ids:
        print(f"  (Email {email_id[:8]}... already in log)")
        return
    ids.add(email_id)

    # Keep only the most recent MAX_LOG_ENTRIES to prevent unbounded growth
    sorted_ids = sorted(list(ids))
    if len(sorted_ids) > MAX_LOG_ENTRIES:
        sorted_ids = sorted_ids[-MAX_LOG_ENTRIES:]
        print(f"  ℹ Trimmed processed log to {MAX_LOG_ENTRIES} entries")

    try:
        with open(LOG_FILE, "w") as f:
            json.dump(sorted_ids, f, indent=2)
        print(f"  ✓ Email {email_id[:8]}... marked as processed ({len(sorted_ids)} total)")
    except Exception as e:
        print(f"  ✗ Failed to save processed email ID: {e}")


def save_processed_id(email_id: str) -> None:
    """
    PUBLIC — called by agent.py at the START of processing each email.
    Ensures email is never re-processed even if Groq fails mid-way.
    """
    _write_processed_id(email_id)


def _save_processed_id(email_id: str) -> None:
    """INTERNAL — secondary mark called by save_draft on success."""
    _write_processed_id(email_id)


# ─────────────────────────────────────────────
# Shared helper — get authenticated services once
# ─────────────────────────────────────────────

_gmail    = None
_calendar = None


def _get_services():
    global _gmail, _calendar
    if _gmail is None or _calendar is None:
        print("\n[Authenticating with Google...]")
        _gmail, _calendar = get_google_services()
    return _gmail, _calendar


# ─────────────────────────────────────────────
# Tool 1 — read_emails
# ─────────────────────────────────────────────

def read_emails(days_back: int = SCAN_DAYS_BACK) -> dict[str, Any]:
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
                    "body":     body[:2000],
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
    blackout_hit, blackout_matches = _is_blackout(start_date, end_date)
    try:
        _, calendar = _get_services()

        try:
            time_max_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            time_max    = f"{time_max_dt.strftime('%Y-%m-%d')}T00:00:00Z"
        except ValueError:
            time_max = f"{end_date}T23:59:59Z"

        result = calendar.events().list(
            calendarId="primary",
            timeMin=f"{start_date}T00:00:00Z",
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        items            = result.get("items", [])
        leave_events     = []
        non_leave_events = []

        for item in items:
            title = item.get("summary", "(untitled)")
            start = item.get("start", {}).get("date") or item.get("start", {}).get("dateTime", "")
            end   = item.get("end",   {}).get("date") or item.get("end",   {}).get("dateTime", "")
            entry = {"title": title, "start": start, "end": end}
            if title.startswith(LEAVE_TAG):
                leave_events.append(entry)
            else:
                non_leave_events.append(entry)

        max_allowed        = LEAVE_POLICY.get("max_people_on_leave", 2)
        people_on_leave    = len(leave_events)
        team_limit_reached = people_on_leave >= max_allowed

        try:
            s          = datetime.strptime(start_date, "%Y-%m-%d")
            e          = datetime.strptime(end_date,   "%Y-%m-%d")
            date_range = f"{s.strftime('%b %d')} - {e.strftime('%b %d, %Y')}"
        except ValueError:
            date_range = f"{start_date} to {end_date}"

        if blackout_hit:
            print(f"    ⚠ Blackout date(s) detected: {blackout_matches}")

        return {
            "leave_events":        leave_events,
            "people_on_leave":     people_on_leave,
            "max_people_on_leave": max_allowed,
            "team_limit_reached":  team_limit_reached,
            "non_leave_events":    non_leave_events,
            "has_conflict":        len(non_leave_events) > 0,
            "conflict_count":      len(non_leave_events),
            "date_range":          date_range,
            "is_blackout_date":    blackout_hit,
            "blackout_dates_hit":  blackout_matches,
            "error":               None,
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
        return {"success": False, "draft_id": "", "to": to_email, "subject": subject, "error": error_msg}
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"  ✗ {error_msg}")
        return {"success": False, "draft_id": "", "to": to_email, "subject": subject, "error": error_msg}


# ─────────────────────────────────────────────
# Tool 4 — add_calendar_event
# ─────────────────────────────────────────────

def add_calendar_event(employee_name: str, start_date: str, end_date: str) -> dict[str, Any]:
    try:
        _, calendar = _get_services()

        end_dt        = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_exclusive = end_dt.strftime("%Y-%m-%d")

        event = {
            "summary":     f"{LEAVE_TAG} {employee_name}",
            "description": f"Approved leave for {employee_name}. Added automatically by Leave Handler.",
            "start":       {"date": start_date},
            "end":         {"date": end_exclusive},
            "colorId":     "6",
        }

        created = calendar.events().insert(calendarId="primary", body=event).execute()

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
        return {"success": False, "event_id": "", "event_title": "", "date_range": "", "calendar_link": "", "error": error_msg}
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"  ✗ {error_msg}")
        return {"success": False, "event_id": "", "event_title": "", "date_range": "", "calendar_link": "", "error": error_msg}


# ─────────────────────────────────────────────
# Tool 5 — notify_slack
# ─────────────────────────────────────────────

def notify_slack(message: str) -> dict[str, Any]:
    try:
        if not SLACK_WEBHOOK_URL:
            error_msg = "SLACK_WEBHOOK_URL not set in .env"
            print(f"  ⚠ {error_msg}")
            return {"success": False, "status": 0, "error": error_msg}

        # Increased timeout from 10s to 20s for GitHub Actions runners
        # which occasionally have slower outbound connections than local machines
        response = requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=20)
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
# Tool dispatcher
# ─────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> dict:
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