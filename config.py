import os
from dotenv import load_dotenv

load_dotenv()

# --- Groq ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# --- Slack ---
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# --- Manager / company info ---
MANAGER_NAME  = "Shanti Priya"
COMPANY_NAME  = "ABC Company"
TEAM_NAME     = "HR Team"

# --- How far back to scan Gmail ---
SCAN_DAYS_BACK = int(os.environ.get("SCAN_DAYS_BACK", "1"))

# --- Leave policy rules (edit these to match your company) ---
LEAVE_POLICY = {
    "min_notice_days": 1,        # Employee must mail BY END OF previous day (23:59)
                                 # e.g. for March 17 leave → email must arrive by March 16 23:59
    "max_consecutive_days": 3,   # Max days in a single request
    "max_people_on_leave": 2,    # Max team members on leave on the same day
    "weekends_off": ["Sunday"],  # These days cannot be taken as leave (already off)
    "blackout_dates": [          # Specific dates no one can take leave (YYYY-MM-DD)
        # "2025-12-31",
        # "2026-01-01",
    ],
}

# --- Gmail search keywords ---
LEAVE_KEYWORDS = [
    "leave request",
    "time off",
    "vacation request",
    "annual leave",
    "sick leave",
    "day off",
    "absence request",
    "emergency leave",
    "request for leave",
]