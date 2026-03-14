"""
main.py — Entry point for the Leave Handler agent.

This is the file you run directly, and the file GitHub Actions will call.

Usage:
    python main.py
"""

import sys
import os
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

    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY is not set in your .env file.")
        print("       Get your key at console.groq.com → API Keys")
        ok = False

    if not SLACK_WEBHOOK_URL:
        print("ERROR: SLACK_WEBHOOK_URL is not set in your .env file.")
        print("       Get it from api.slack.com → Your App → Incoming Webhooks")
        ok = False

    if not os.path.exists("credentials.json"):
        print("ERROR: credentials.json not found in this folder.")
        print("       Download it from Google Cloud Console → APIs & Services → Credentials")
        ok = False

    return ok


def main():
    print("Leave Handler — starting up")
    print(f"Working directory: {os.getcwd()}\n")

    # Validate config before doing anything
    if not check_config():
        print("\nFix the errors above and try again.")
        sys.exit(1)

    print("Config OK — starting agent...\n")

    # Run the MCP agent loop
    from agent import run_agent
    run_agent()


if __name__ == "__main__":
    main()