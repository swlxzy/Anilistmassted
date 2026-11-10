import subprocess
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import webbrowser

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

REDIRECT_URI = "http://localhost:8080/callback"

AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
TOKEN_URL = "https://anilist.co/api/v2/oauth/token"


def load_config():
    if not CONFIG_FILE.exists():
        return {}

    try:
        return json.loads(
            CONFIG_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {}


def save_config(config):
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2),
        encoding="utf-8"
    )


def get_credentials():
    config = load_config()

    client_id = config.get("client_id")
    client_secret = config.get("client_secret")

    if not client_id:
        client_id = input("AniList Client ID: ").strip()

    if not client_secret:
        client_secret = input("AniList Client Secret: ").strip()

    if not client_id or not client_secret:
        raise RuntimeError(
            "Client ID and Client Secret are required."
        )

    config["client_id"] = client_id
    config["client_secret"] = client_secret

    save_config(config)

    return client_id, client_secret


class CallbackHandler(BaseHTTPRequestHandler):
    authorization_code = None
    error = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            CallbackHandler.error = params["error"][0]

            message = (
                "<html><body>"
                "<h2>AniList login failed.</h2>"
                "<p>You can close this page.</p>"
                "</body></html>"
            )

        elif "code" in params:
            CallbackHandler.authorization_code = params["code"][0]

            message = (
                "<html><body>"
                "<h2> AniList login successful! </h2>"
                "<p>You can close this page and return to Termux.</p>"
                "</body></html>"
            )

        else:
            CallbackHandler.error = "No authorization code received."

            message = (
                "<html><body>"
                "<h2>No authorization code received.</h2>"
                "</body></html>"
            )

        body = message.encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def exchange_code(client_id, client_secret, code):
    payload = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    })

    try:
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-X", "POST",
                TOKEN_URL,
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "-H", "Accept: application/json",
                "--data", payload,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Token request timed out."
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"curl failed: {result.stderr.strip()}"
        )

    raw = result.stdout.strip()

    print()
    print("AniList token response:")
    print(raw)
    print()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(
            "AniList returned an invalid response."
        )

    if "access_token" not in data:
        raise RuntimeError(
            "AniList did not return an access token."
        )

    return data


def login():
    client_id, client_secret = get_credentials()

    CallbackHandler.authorization_code = None
    CallbackHandler.error = None

    state = secrets.token_urlsafe(32)

    server = HTTPServer(
        ("127.0.0.1", 8080),
        CallbackHandler
    )

    authorization_url = (
        f"{AUTHORIZE_URL}?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "state": state,
        })
    )

    print()
    print("Opening AniList login...")
    print()
    print("If the browser does not open automatically,")
    print("open this URL manually:")
    print()
    print(authorization_url)
    print()

    try:
        webbrowser.open(authorization_url)
    except Exception:
        pass

    print("Waiting for AniList callback...")

    server.timeout = 1

    start_time = time.time()

    while (
        CallbackHandler.authorization_code is None
        and CallbackHandler.error is None
        and time.time() - start_time < 300
    ):
        server.handle_request()

    server.server_close()

    if CallbackHandler.error:
        raise RuntimeError(
            f"AniList OAuth error: "
            f"{CallbackHandler.error}"
        )

    if not CallbackHandler.authorization_code:
        raise RuntimeError(
            "Timed out waiting for AniList login."
        )

    print("Authorization received.")
    print("Getting access token...")

    token_data = exchange_code(
        client_id,
        client_secret,
        CallbackHandler.authorization_code
    )

    config = load_config()

    config["access_token"] = token_data["access_token"]

    if "expires_in" in token_data:
        config["expires_in"] = token_data["expires_in"]

    save_config(config)

    print("Access token saved.")

    return token_data["access_token"]


def get_access_token():
    config = load_config()

    token = config.get("access_token")

    if token:
        return token

    return login()


if __name__ == "__main__":
    print("AniList Authentication")
    print("======================")

    get_access_token()

    print()
    print("Authentication complete.")