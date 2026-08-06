#!/usr/bin/env python3
"""One-off helper: turn a Google OAuth client into a Gmail REFRESH TOKEN.

Run this ONCE, on your own laptop, after creating the OAuth client in Google
Cloud (see NEWSLETTER_SETUP.md step 2). It prints a refresh token; paste that
into the GOOGLE_REFRESH_TOKEN environment variable in Render.

    python3 scripts/get_gmail_refresh_token.py

WHY THIS RUNS LOCALLY AND NOT ON THE SERVER
Getting a refresh token requires a human to click "Allow" in a browser while
signed in as timothy.honey@beardedsealstudios.com. Building that consent flow
into the live server would mean the production service permanently exposes an
OAuth callback that can mint sending credentials — a real attack surface, for
something done once. Doing it here means the server only ever holds the
finished token, and the client secret never has to live anywhere but Render.

WHAT IT ASKS GOOGLE FOR
    https://www.googleapis.com/auth/gmail.send   send only, cannot read mail
    openid email                                 so the server can verify WHICH
                                                 account it is authorised as
Nothing else. If Google's consent screen offers more, something is wrong.

SECURITY
  • The refresh token is printed to your terminal and NOTHING else. It is not
    written to a file, not copied to the clipboard, not logged.
  • Treat it exactly like a password: it can send email as you until revoked.
  • Revoke any time at https://myaccount.google.com/permissions
  • Never commit it. NEWSLETTER_SETUP.md and render.yaml deliberately contain
    only the variable NAME.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/gmail.send openid email"

# Google requires the redirect URI to match the OAuth client EXACTLY. 127.0.0.1
# (not "localhost") is what Google's own docs use for installed apps, and the
# port must be the one registered on the client.
DEFAULT_PORT = 8765

_result: dict = {}
_done = threading.Event()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth2callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        _result["code"] = (qs.get("code") or [""])[0]
        _result["state"] = (qs.get("state") or [""])[0]
        _result["error"] = (qs.get("error") or [""])[0]
        body = (b"<html><body style='font-family:sans-serif;text-align:center;"
                b"padding:60px;background:#04263b;color:#eaf6fb'>"
                b"<h2>Done &mdash; you can close this tab.</h2>"
                b"<p>Go back to your terminal for the refresh token.</p>"
                b"</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _done.set()

    def log_message(self, *args):  # silence the default access log
        return


def _prompt(label: str, env: str) -> str:
    val = os.environ.get(env, "").strip()
    if val:
        print("Using %s from the environment." % env)
        return val
    try:
        val = input(label).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(1)
    if not val:
        print("That can't be blank.")
        sys.exit(1)
    return val


def main() -> int:
    print(__doc__)
    print("=" * 70)
    client_id = _prompt("Paste your GOOGLE_CLIENT_ID: ", "GOOGLE_CLIENT_ID")
    client_secret = _prompt("Paste your GOOGLE_CLIENT_SECRET: ", "GOOGLE_CLIENT_SECRET")

    port = int(os.environ.get("OAUTH_PORT", DEFAULT_PORT))
    redirect_uri = "http://127.0.0.1:%d/oauth2callback" % port
    print()
    print("The redirect URI must be registered on the OAuth client, EXACTLY:")
    print("    %s" % redirect_uri)
    print("(Google Cloud Console → APIs & Services → Credentials → your OAuth")
    print(" client → Authorised redirect URIs. A trailing slash counts.)")
    print()

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        print("Could not listen on port %d: %s" % (port, exc))
        print("Set OAUTH_PORT to a free port and register that redirect URI too.")
        return 1

    state = secrets.token_urlsafe(24)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        # offline + consent is what makes Google return a REFRESH token.
        # Without prompt=consent, a repeat authorisation returns only an access
        # token and the script would appear to "work" while giving you nothing
        # that survives an hour.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    url = AUTH_URI + "?" + urllib.parse.urlencode(params)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Opening your browser. Sign in as the account that will SEND the")
    print("newsletters (timothy.honey@beardedsealstudios.com) and click Allow.")
    print()
    print("If the browser doesn't open, paste this URL into it:")
    print(url)
    print()
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass

    if not _done.wait(timeout=300):
        print("Timed out waiting for the browser. Nothing was changed.")
        return 1
    server.shutdown()

    if _result.get("error"):
        print("Google returned an error: %s" % _result["error"])
        return 1
    if _result.get("state") != state:
        # A mismatched state means the response did not come from the request
        # this script made.
        print("State mismatch — aborting rather than trusting that response.")
        return 1
    code = _result.get("code") or ""
    if not code:
        print("No authorisation code came back.")
        return 1

    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URI, data=payload, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("Token exchange failed: %s" % exc)
        return 1

    refresh = data.get("refresh_token")
    if not refresh:
        print("Google did not return a refresh token.")
        print("This usually means the account has authorised this client before.")
        print("Remove it at https://myaccount.google.com/permissions and re-run.")
        return 1

    # Confirm WHICH account was authorised, so a wrong-account authorisation is
    # caught here rather than as a confusing 'wrong account' banner in the app.
    who = ""
    try:
        with urllib.request.urlopen(
            "https://oauth2.googleapis.com/tokeninfo?access_token="
            + urllib.parse.quote(data.get("access_token", "")), timeout=20
        ) as resp:
            who = json.loads(resp.read().decode("utf-8")).get("email", "")
    except Exception:  # noqa: BLE001
        pass

    print()
    print("=" * 70)
    print("SUCCESS")
    if who:
        print("Authorised as: %s" % who)
        print("This MUST match GMAIL_SENDER_EMAIL in Render, or Gmail will")
        print("refuse to send with that From address.")
    print()
    print("Add this to Render → your service → Environment:")
    print()
    print("  GOOGLE_REFRESH_TOKEN = %s" % refresh)
    print()
    print("Then redeploy. Do not commit this value or paste it into a chat.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
