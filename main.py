# """
# main.py — Entry point for the Leave Handler agent.

# This is the file you run directly, and the file GitHub Actions will call.

# Usage:
#     python main.py
# """

# import sys
# import os
# from datetime import datetime
# from dotenv import load_dotenv

# # Load .env file so all environment variables are available
# load_dotenv()

# from config import GROQ_API_KEY, SLACK_WEBHOOK_URL


# def check_config() -> bool:
#     """
#     Validates that all required config is present before starting.
#     Prints a clear error message if anything is missing.
#     Returns True if all good, False if something is missing.
#     """
#     ok = True

#     print("Validating configuration...")
#     print()

#     # Check Groq API Key
#     if not GROQ_API_KEY:
#         print("✗ ERROR: GROQ_API_KEY is not set in your .env file.")
#         print("  Get your key at console.groq.com → API Keys")
#         ok = False
#     else:
#         print(f"✓ GROQ_API_KEY: {GROQ_API_KEY[:20]}...")

#     # Check Slack Webhook
#     if not SLACK_WEBHOOK_URL:
#         print("✗ ERROR: SLACK_WEBHOOK_URL is not set in your .env file.")
#         print("  Get it from api.slack.com → Your App → Incoming Webhooks")
#         ok = False
#     else:
#         print(f"✓ SLACK_WEBHOOK_URL: {SLACK_WEBHOOK_URL[:40]}...")

#     # Check Google credentials
#     if not os.path.exists("credentials.json"):
#         print("✗ ERROR: credentials.json not found in this folder.")
#         print("  Download it from Google Cloud Console → APIs & Services → Credentials")
#         ok = False
#     else:
#         print("✓ credentials.json: Found")

#     # Check Google token (optional, will be created if missing)
#     if not os.path.exists("token.json"):
#         print("⚠ WARNING: token.json not found.")
#         print("  This is normal for first run - OAuth flow will create it.")
#         print("  If running in GitHub Actions, ensure GOOGLE_TOKEN secret is set.")
#     else:
#         print("✓ token.json: Found")

#     # Check processed emails log (optional, will be created if missing)
#     if not os.path.exists("processed_emails.json"):
#         print("⚠ INFO: processed_emails.json not found.")
#         print("  This is normal for first run - will be created automatically.")
#     else:
#         print("✓ processed_emails.json: Found")

#     print()
#     return ok


# def main():
#     print("=" * 70)
#     print("  LEAVE HANDLER AGENT")
#     print("=" * 70)
#     print(f"  Current time (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
#     print(f"  Working directory: {os.getcwd()}")
#     print("=" * 70)
#     print()

#     # Validate config before doing anything
#     if not check_config():
#         print()
#         print("=" * 70)
#         print("  CONFIGURATION ERRORS FOUND")
#         print("=" * 70)
#         print("  Fix the errors above and try again.")
#         print("=" * 70)
#         sys.exit(1)

#     print("=" * 70)
#     print("  Configuration OK — starting agent")
#     print("=" * 70)
#     print()

#     # Run the MCP agent loop
#     try:
#         from agent import run_agent
#         result = run_agent()
        
#         print()
#         print("=" * 70)
#         print("  AGENT COMPLETED SUCCESSFULLY")
#         print("=" * 70)
#         print()
        
#         sys.exit(0)
        
#     except KeyboardInterrupt:
#         print()
#         print("=" * 70)
#         print("  AGENT INTERRUPTED BY USER")
#         print("=" * 70)
#         sys.exit(130)
        
#     except Exception as e:
#         print()
#         print("=" * 70)
#         print("  AGENT FAILED WITH ERROR")
#         print("=" * 70)
#         print(f"  Error: {e}")
#         print()
#         import traceback
#         traceback.print_exc()
#         print("=" * 70)
#         sys.exit(1)


# if __name__ == "__main__":
#     main()

"""
main.py — Entry point for the Leave Handler agent.

This is the file you run directly, and the file GitHub Actions will call.

Usage:
    python main.py
"""

import sys
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load .env file so all environment variables are available
load_dotenv()

from config import GROQ_API_KEY, SLACK_WEBHOOK_URL


def check_config() -> bool:
    """
    Validates that all required config is present before starting.
    Prints a clear error message if anything is missing.
    Returns True if all good, False if something is missing.
    """
    ok = True

    print("Validating configuration...")
    print()

    # Check Groq API Key
    if not GROQ_API_KEY:
        print("✗ ERROR: GROQ_API_KEY is not set in your .env file.")
        print("  Get your key at console.groq.com → API Keys")
        ok = False
    else:
        print(f"✓ GROQ_API_KEY: {GROQ_API_KEY[:20]}...")

    # Check Slack Webhook
    if not SLACK_WEBHOOK_URL:
        print("✗ ERROR: SLACK_WEBHOOK_URL is not set in your .env file.")
        print("  Get it from api.slack.com → Your App → Incoming Webhooks")
        ok = False
    else:
        print(f"✓ SLACK_WEBHOOK_URL: {SLACK_WEBHOOK_URL[:40]}...")

    # Check Google OAuth env vars (replaces credentials.json + token.json)
    google_client_id     = os.environ.get("GOOGLE_CLIENT_ID",     "").strip()
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    google_refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()

    if not google_client_id:
        print("✗ ERROR: GOOGLE_CLIENT_ID is not set.")
        print("  Get it from Google Cloud Console → APIs & Services → Credentials")
        ok = False
    else:
        print(f"✓ GOOGLE_CLIENT_ID: {google_client_id[:20]}...")

    if not google_client_secret:
        print("✗ ERROR: GOOGLE_CLIENT_SECRET is not set.")
        print("  Get it from Google Cloud Console → APIs & Services → Credentials")
        ok = False
    else:
        print(f"✓ GOOGLE_CLIENT_SECRET: {google_client_secret[:6]}...")

    if not google_refresh_token:
        print("✗ ERROR: GOOGLE_REFRESH_TOKEN is not set.")
        print("  Get it from https://developers.google.com/oauthplayground")
        print("  See auth.py docstring for full instructions.")
        ok = False
    else:
        print(f"✓ GOOGLE_REFRESH_TOKEN: {google_refresh_token[:20]}...")

    # Check processed emails log (optional — created automatically if missing)
    if not os.path.exists("processed_emails.json"):
        print("⚠ INFO: processed_emails.json not found.")
        print("  This is normal for first run — will be created automatically.")
    else:
        print("✓ processed_emails.json: Found")

    print()
    return ok


def main():
    print("=" * 70)
    print("  LEAVE HANDLER AGENT")
    print("=" * 70)
    # Use timezone-aware UTC time (fixes DeprecationWarning from datetime.utcnow)
    print(f"  Current time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Working directory:  {os.getcwd()}")
    print("=" * 70)
    print()

    if not check_config():
        print()
        print("=" * 70)
        print("  CONFIGURATION ERRORS FOUND")
        print("=" * 70)
        print("  Fix the errors above and try again.")
        print("=" * 70)
        sys.exit(1)

    print("=" * 70)
    print("  Configuration OK — starting agent")
    print("=" * 70)
    print()

    try:
        from agent import run_agent
        run_agent()

        print()
        print("=" * 70)
        print("  AGENT COMPLETED SUCCESSFULLY")
        print("=" * 70)
        print()

        sys.exit(0)

    except KeyboardInterrupt:
        print()
        print("=" * 70)
        print("  AGENT INTERRUPTED BY USER")
        print("=" * 70)
        sys.exit(130)

    except Exception as e:
        print()
        print("=" * 70)
        print("  AGENT FAILED WITH ERROR")
        print("=" * 70)
        print(f"  Error: {e}")
        print()
        import traceback
        traceback.print_exc()
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()