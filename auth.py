"""
auth.py — Google OAuth authentication with enhanced error handling.

Handles token refresh automatically and logs when tokens are updated.
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def get_google_services():
    """
    Authenticates with Google and returns Gmail + Calendar service objects.
    
    Token handling:
    - First run: Opens browser for OAuth login, saves token.json
    - Later runs: Loads token.json silently, refreshes if expired
    - If token refreshes: Updates token.json file (GitHub Actions must save this!)
    
    Returns:
        tuple: (gmail_service, calendar_service)
    
    Raises:
        FileNotFoundError: If credentials.json is missing
        Exception: If authentication fails
    """
    
    if not os.path.exists("credentials.json"):
        raise FileNotFoundError(
            "credentials.json not found. "
            "Download it from Google Cloud Console → APIs & Services → Credentials"
        )
    
    creds = None
    token_was_refreshed = False

    # ── Try to load existing token ──
    if os.path.exists("token.json"):
        print("  Loading existing token.json...")
        try:
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
            print("  ✓ Token loaded successfully")
        except Exception as e:
            print(f"  ⚠ Failed to load token.json: {e}")
            creds = None

    # ── Refresh or get new credentials ──
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("  Token expired, refreshing...")
            try:
                creds.refresh(Request())
                token_was_refreshed = True
                print("  ✓ Token refreshed successfully")
            except Exception as e:
                print(f"  ✗ Token refresh failed: {e}")
                print("  Will attempt to re-authenticate...")
                creds = None
        
        # If refresh failed or no token exists, do full OAuth flow
        if not creds:
            print("  Starting OAuth flow (browser will open)...")
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
            token_was_refreshed = True
            print("  ✓ Authentication successful")

    # ── Save token if it was refreshed ──
    if token_was_refreshed:
        print("  Saving updated token.json...")
        try:
            with open("token.json", "w") as token_file:
                token_file.write(creds.to_json())
            print("  ✓ Token saved (GitHub Actions must upload this as artifact!)")
        except Exception as e:
            print(f"  ⚠ Failed to save token.json: {e}")

    # ── Build service objects ──
    print("  Building Gmail and Calendar services...")
    try:
        gmail_service = build("gmail", "v1", credentials=creds)
        calendar_service = build("calendar", "v3", credentials=creds)
        print("  ✓ Services ready")
        return gmail_service, calendar_service
    except Exception as e:
        raise Exception(f"Failed to build Google API services: {e}")