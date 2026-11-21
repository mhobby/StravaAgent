"""OAuth for the Strava REST API (v3), using your own Strava API application.

Your app's credentials are read from the files CLIENT_ID and CLIENT_SECRET
(a .txt extension, as Windows Notepad adds, is also accepted).

The MCP server logs in at startup via ensure_tokens(). To log in on its own:

    uv run python auth.py

Tokens are stored in .strava_tokens.json (gitignored) and refreshed
automatically by RefreshingOAuthAuth. Strava rotates refresh tokens, so the
file is rewritten on every refresh. Messages go to stderr so they never mix
with an MCP server's stdout.
"""

import asyncio
import json
import secrets
import subprocess
import sys
import time
import webbrowser
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

ROOT = Path(__file__).resolve().parent
TOKENS_PATH = ROOT / ".strava_tokens.json"
LOGIN_TIMEOUT_SECONDS = 300


def _read_credential(name: str) -> str:
    """Read CLIENT_ID / CLIENT_SECRET from the project folder (or NAME.txt)."""
    for path in (ROOT / name, ROOT / f"{name}.txt"):
        if path.exists():
            value = path.read_text(encoding="utf-8-sig").strip()  # -sig: Notepad BOM
            if value:
                return value
    raise RuntimeError(
        f"Missing Strava credential: create a file called {name} in {ROOT} "
        "containing the value from https://www.strava.com/settings/api"
    )


STRAVA_CLIENT_ID = _read_credential("CLIENT_ID")
STRAVA_CLIENT_SECRET = _read_credential("CLIENT_SECRET")
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_SCOPES = "read,activity:read_all,profile:read_all"
REDIRECT_URI = "http://127.0.0.1:8765/callback"


def claude_login():
    subprocess.run(["claude", "auth", "login", "--claudeai"], check=False)


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp in seconds

    @property
    def is_expired(self) -> bool:
        # Refresh 60 seconds early to avoid race conditions mid-request
        return time.time() >= (self.expires_at - 60)

    def save(self, path: Path = TOKENS_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))
        path.chmod(0o600)

    @classmethod
    def load(cls, path: Path = TOKENS_PATH) -> "OAuthTokens":
        if not path.exists():
            raise RuntimeError(
                f"No Strava tokens at {path}. Run `uv run python auth.py` to log in."
            )
        return cls(**json.loads(path.read_text()))

    async def refresh(self) -> None:
        """Exchange the refresh_token for a new access token, mutating in place."""
        payload = {
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(STRAVA_TOKEN_URL, data=payload)
            resp.raise_for_status()
            body = resp.json()

        self.access_token = body["access_token"]
        self.refresh_token = body.get("refresh_token", self.refresh_token)
        self.expires_at = float(
            body.get("expires_at") or time.time() + float(body.get("expires_in", 3600))
        )
        self.save()


class RefreshingOAuthAuth(httpx.Auth):
    """httpx.Auth adapter that injects a Bearer token and refreshes on expiry / 401."""

    def __init__(self, tokens: OAuthTokens) -> None:
        self._lock = asyncio.Lock()
        self._tokens = tokens

    async def _ensure_fresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if force or self._tokens.is_expired:
                await self._tokens.refresh()

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        await self._ensure_fresh()
        request.headers["Authorization"] = f"Bearer {self._tokens.access_token}"
        response = yield request

        # Retry once on 401 (clock skew, revoked token, etc.)
        if response.status_code == 401:
            await self._ensure_fresh(force=True)
            request.headers["Authorization"] = f"Bearer {self._tokens.access_token}"
            yield request


def authorize_strava() -> OAuthTokens:
    """Run the browser OAuth flow, save tokens to TOKENS_PATH and return them."""
    expected_state = secrets.token_urlsafe(16)
    captured: dict[str, str | None] = {"code": None, "state": None, "error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(parsed.query)
            captured["code"] = params.get("code", [None])[0]
            captured["state"] = params.get("state", [None])[0]
            captured["error"] = params.get("error", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h2>Strava authorization complete.</h2><p>You can close this tab and return to your terminal.</p>"
            )

        def log_message(self, format: str, *args) -> None:
            # Silence default HTTP server request logs in terminal
            return

    auth_query = urlencode(
        {
            "client_id": STRAVA_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "approval_prompt": "force",
            "scope": STRAVA_SCOPES,
            "state": expected_state,
        }
    )
    auth_url = f"{STRAVA_AUTH_URL}?{auth_query}"

    server = HTTPServer(("127.0.0.1", 8765), CallbackHandler)
    server.timeout = LOGIN_TIMEOUT_SECONDS
    print(
        f"Opening browser to authorize Strava app...\nIf it doesn't open, visit:\n{auth_url}\n",
        file=sys.stderr,
    )
    webbrowser.open(auth_url)

    # Handle the single OAuth redirect callback (or time out) and shut down
    try:
        server.handle_request()
    finally:
        server.server_close()

    if captured["error"] is not None:
        raise RuntimeError(f"Strava authorization failed: {captured['error']}")
    if captured["code"] is None:
        raise RuntimeError(
            f"No Strava authorization within {LOGIN_TIMEOUT_SECONDS}s. "
            "Retry, or log in ahead of time with `uv run python auth.py`."
        )
    if captured["state"] != expected_state:
        raise RuntimeError("OAuth state mismatch — aborting for safety.")

    token_resp = httpx.post(
        STRAVA_TOKEN_URL,
        data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": captured["code"],
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    token_resp.raise_for_status()
    payload = token_resp.json()

    tokens = OAuthTokens(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=float(payload["expires_at"]),
    )
    tokens.save()
    return tokens


def ensure_tokens() -> OAuthTokens:
    """Load saved tokens, or run the browser login if there are none.

    Not safe to call from several processes at once (each would open a login).
    That's fine for the single long-running HTTP MCP server; concurrent calls
    inside it are serialised by StravaAPI's asyncio lock.
    """
    if TOKENS_PATH.exists():
        return OAuthTokens.load()
    return authorize_strava()


if __name__ == "__main__":
    authorize_strava()
    print(f"Saved Strava tokens to {TOKENS_PATH}", file=sys.stderr)
