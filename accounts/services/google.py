import os
from google.oauth2 import id_token
from google.auth.transport import requests

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

def verify_google_id_token(token: str) -> dict:
    if not GOOGLE_CLIENT_ID:
        raise ValueError("Google client id not configured")
    info = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
    if info.get("aud") != GOOGLE_CLIENT_ID:
        raise ValueError("Invalid audience")
    return info  # contains email, email_verified, sub, given_name, family_name, picture, etc.
