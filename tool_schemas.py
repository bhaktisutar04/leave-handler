"""
tool_schemas.py — JSON schema definitions of the 5 MCP tools.
Sent to Groq so it knows what tools exist and when to call them.
"""

MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": (
                "Search Gmail for NEW leave request emails. "
                "Always call this first. Already-processed emails are skipped automatically. "
                "Returns emails with sender, subject, body, date, and email_id. "
                "Call this with no arguments."
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
                "Check Google Calendar for the requested leave dates. Does two things: "
                "(1) finds any existing events (conflicts), "
                "(2) counts how many team members are already on approved leave those days by looking for [LEAVE] events. "
                "If team_limit_reached is True, you MUST decline — the team cannot have more people off. "
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
                        "description": (
                            "Full email body. Write this yourself — personalized and human. "
                            "Approvals: confirm dates, wish them well. "
                            "Declines: explain reason clearly and kindly. "
                            "Sign off with the manager's name."
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
                "Call this exactly once at the very end after all requests are processed. "
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