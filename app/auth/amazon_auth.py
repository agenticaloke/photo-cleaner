import secrets

import requests
from flask import Blueprint, redirect, request, session, url_for, current_app

amazon_auth_bp = Blueprint("amazon_auth", __name__, url_prefix="/auth/amazon")

AUTH_URL   = "https://www.amazon.com/ap/oa"
TOKEN_URL  = "https://api.amazon.com/auth/o2/token"
SCOPES     = "profile clouddrive:read_all clouddrive:write"


@amazon_auth_bp.route("/login")
def login():
    state = secrets.token_urlsafe(32)
    session["amazon_oauth_state"] = state

    params = {
        "client_id":     current_app.config["AMAZON_CLIENT_ID"],
        "scope":         SCOPES,
        "response_type": "code",
        "redirect_uri":  current_app.config["AMAZON_REDIRECT_URI"],
        "state":         state,
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return redirect(f"{AUTH_URL}?{qs}")


@amazon_auth_bp.route("/callback")
def callback():
    if request.args.get("state") != session.get("amazon_oauth_state"):
        return "Invalid state parameter", 403

    if "error" in request.args:
        return f"OAuth error: {request.args.get('error_description', request.args.get('error'))}", 400

    code = request.args.get("code")
    if not code:
        return "Missing authorization code", 400

    resp = requests.post(TOKEN_URL, data={
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  current_app.config["AMAZON_REDIRECT_URI"],
        "client_id":     current_app.config["AMAZON_CLIENT_ID"],
        "client_secret": current_app.config["AMAZON_CLIENT_SECRET"],
    }, timeout=15)

    if resp.status_code != 200:
        return f"Token exchange failed: {resp.text}", 400

    data = resp.json()
    if "error" in data:
        return f"Token error: {data.get('error_description', data['error'])}", 400

    session["amazon_token"] = data["access_token"]
    session["amazon_refresh_token"] = data.get("refresh_token")
    session["amazon_connected"] = True
    session.pop("amazon_oauth_state", None)

    return redirect(url_for("web.index"))


@amazon_auth_bp.route("/logout")
def logout():
    session.pop("amazon_token", None)
    session.pop("amazon_refresh_token", None)
    session.pop("amazon_connected", None)
    session.pop("amazon_oauth_state", None)
    return redirect(url_for("web.index"))
