"""
One-time YouTube Authorization Script
---------------------------------------
Run this FIRST, before running main.py.
It only handles the Google login/permission step and creates token.json.
No video editing or Pexels needed for this step.
"""

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    print("\nSUCCESS! token.json has been created.")
    print("You can now proceed to the next step.")


if __name__ == "__main__":
    main()
