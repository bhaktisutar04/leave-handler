"""
tool_schemas.py — JSON schema definitions of the 5 MCP tools.
Sent to Groq so it knows what tools exist and when to call them.

FIXES:
  - check_calendar description updated to document is_blackout_date and
    blackout_dates_hit response fields so Groq treats them as hard decline signals
"""

MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": (
                "Search Gmail for NEW leave request emails. "
                "Already-processed emails are skipped automatically. "
                "Returns emails with sender, subject, body, date, and email_id. "
                "NOTE: This is called by Python directly — you do not need to call it."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_calendar",
            "description": (
                "Check Google Calendar for the requested leave dates. Does three things: "
                "(1) counts team members already on approved leave (leave_events / people_on_leave), "
                "(2) finds real scheduling conflicts — meetings, holidays, etc. (non_leave_events / has_conflict), "
                "(3) checks if any requested date is a company blackout date. "
                "IMPORTANT: other team members' [LEAVE] events are NOT a conflict reason — "
                "they only contribute to the people_on_leave headcount. "
                "Do NOT decline a request just because has_conflict is True or non_leave_events is non-empty — "
                "those are informational only. "
                "Hard decline signals: "
                "- team_limit_reached=True → too many people already off. "
                "- is_blackout_date=True → the date is a company blackout day. "
                "Always call this before deciding to approve or decline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Leave start date in YYYY-MM-DD format.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Leave end date in YYYY-MM-DD format.",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_draft",
            "description": (
                "Save an email reply as a Gmail Draft. "
                "Call this for EVERY request — approvals and declines. "
                "Always pass the email_id so the original email is marked as processed. "
                "Write a warm, personalized reply — not a template."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_email": {
                        "type": "string",
                        "description": "Employee's email address from the sender field.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Reply subject. Example: 'Re: Leave Request — Mar 20-22'",
                    },
                    "body": {
                        "type": "string",
                        "maxLength": 1000,
                        "description": (
                            "Full email body. Keep it concise — 3 to 5 sentences maximum. "
                            "Approvals: confirm dates, wish them well. "
                            "Declines: explain reason clearly and kindly in 2-3 sentences. "
                            "Sign off with the manager's name. "
                            "Do NOT include your reasoning or policy analysis in the body."
                        ),
                    },
                    "email_id": {
                        "type": "string",
                        "description": "The email_id from the original leave request. Always pass this.",
                    },
                },
                "required": ["to_email", "subject", "body", "email_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_calendar_event",
            "description": (
                "Add an approved leave entry to Google Calendar. "
                "Only call this when you are APPROVING a request — never for declines. "
                "Call this AFTER save_draft for approvals. "
                "This creates a [LEAVE] event so future requests on the same dates "
                "will correctly count how many people are already off."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_name": {
                        "type": "string",
                        "description": "Full name of the employee. Example: 'Bhakti Sutar'",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Leave start date in YYYY-MM-DD format.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Last day of leave in YYYY-MM-DD format.",
                    },
                },
                "required": ["employee_name", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_slack",
            "description": (
                "Post a summary to the #leave-alerts Slack channel. "
                "NOTE: This is called by Python directly after all emails are processed — "
                "you do not need to call it. "
                "Include: total processed, who was approved, who was declined, and why."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Summary message. Include names, outcomes, and any flags for manual review.",
                    }
                },
                "required": ["message"],
            },
        },
    },
]