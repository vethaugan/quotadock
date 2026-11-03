"""Read quota without making inference requests or storing account credentials."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from .models import Snapshot, parse_claude, parse_codex, parse_gemini


class ProviderError(Exception):
    def __init__(self, status, detail, retry_after=300):
        self.status = status
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(detail)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, token, payload=None, headers=None):
    # Destinations are fixed by the adapters, never supplied by an API response.
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                 "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ProviderError("Unavailable", "The provider returned an unexpectedly large response.")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Expected object")
            return result
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise ProviderError("Sign-in needed", "Open the provider's CLI and sign in again, then refresh.") from None
        if error.code == 403:
            raise ProviderError("Access denied", "The provider rejected this quota read. Check your account access and CLI sign-in.") from None
        if error.code == 429:
            retry = error.headers.get("Retry-After", "900")
            try:
                delay = max(900, int(retry))
            except ValueError:
                delay = 900
            raise ProviderError("Rate limited", "The provider asked us to wait before refreshing.", delay) from None
        raise ProviderError("Unavailable", f"The provider returned HTTP {error.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("Offline", "Could not reach the provider. The last reading may be out of date.") from None
    except (ValueError, UnicodeError):
        raise ProviderError("Unavailable", "The provider response format was not recognized.") from None


def read_credentials(path, instruction):
    try:
        with path.open() as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise ProviderError("Sign-in needed", instruction) from None


class CodexRPC:
    """Short-lived stdio connection with a deadline and no conversation reads."""

    def __enter__(self):
        executable = shutil.which("codex")
        if not executable:
            raise ProviderError("Setup needed", "Install Codex CLI and run codex login.")
        self.process = subprocess.Popen(
            [executable, "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.sequence = 0
        self.deadline = time.monotonic() + 35
        return self

    def send(self, message):
        try:
            self.process.stdin.write(json.dumps(message).encode()+b"\n")
            self.process.stdin.flush()
        except OSError:
            raise ProviderError("Unavailable", "Codex app-server closed its connection.") from None

    def call(self, method, params=None):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params or {}})
        while time.monotonic() < self.deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(reply, dict) or reply.get("id") != request_id:
                    continue
                if "error" in reply:
                    raise ProviderError("Unavailable", "Codex could not read quota. Check codex login status and try again.")
                return reply.get("result", {})
            if self.selector.select(timeout=max(0, self.deadline-time.monotonic())):
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise ProviderError("Unavailable", "Codex app-server stopped before returning quota.")
                self.buffer += chunk
                if len(self.buffer) > 2_097_152:
                    raise ProviderError("Unavailable", "Codex returned an unexpectedly large response.")
        raise ProviderError("Timeout", "Codex did not return quota within 35 seconds.")

    def __exit__(self, *args):
        self.selector.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def fetch_codex():
    with CodexRPC() as rpc:
        rpc.call("initialize", {"clientInfo": {"name": "quotadock", "title": "QuotaDock", "version": "0.1.0"}})
        rpc.send({"method": "initialized"})
        account = rpc.call("account/read", {"refreshToken": False}).get("account")
        if not isinstance(account, dict) or account.get("type") not in ("chatgpt", "chatgptAuthTokens"):
            raise ProviderError("Sign-in needed", "Run codex login with your ChatGPT subscription account.")
        quotas = parse_codex(rpc.call("account/rateLimits/read"))
    return snapshot("OpenAI", "Shared plan · Codex, Work, Agents & Excel", quotas,
                    "Chat conversations are not included in this plan quota.")


def fetch_claude():
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home()/".claude")))
    credentials = read_credentials(directory/".credentials.json", "Install Claude Code and run claude auth login.")
    oauth = credentials.get("claudeAiOauth")
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token:
        raise ProviderError("Sign-in needed", "Run claude auth login with your Claude subscription account.")
    data = request_json("https://api.anthropic.com/api/oauth/usage", token, headers={"anthropic-beta": "oauth-2025-04-20"})
    return snapshot("Anthropic", "Claude Code · shared Claude quota", parse_claude(data))


def fetch_gemini():
    credentials = read_credentials(Path.home()/".gemini/oauth_creds.json", "Install Gemini CLI and sign in with Google. API-key quotas are not subscription quotas.")
    token = credentials.get("access_token")
    expiry = credentials.get("expiry_date")
    if not isinstance(token, str) or not token or (isinstance(expiry, (int, float)) and expiry <= time.time()*1000):
        raise ProviderError("Sign-in needed", "Open Gemini CLI to renew its Google sign-in, then refresh. QuotaDock does not modify CLI credentials.")
    base = "https://cloudcode-pa.googleapis.com/v1internal:"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
    if not project:
        data = request_json(base+"loadCodeAssist", token, {"metadata": {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}})
        project = data.get("cloudaicompanionProject")
    if not isinstance(project, str) or not project:
        raise ProviderError("Setup needed", "Finish Gemini CLI setup, or set GOOGLE_CLOUD_PROJECT for your Code Assist subscription.")
    data = request_json(base+"retrieveUserQuota", token, {"project": project})
    return snapshot("Google", "Gemini CLI", parse_gemini(data))


def snapshot(provider, product, quotas, detail=""):
    return Snapshot(provider, product, quotas, "Live" if quotas else "Not reported", detail if quotas else "No percentage quota was reported for this account.", datetime.now(timezone.utc))


PROVIDERS = {
    "codex": ("OpenAI", "Shared OpenAI plan", fetch_codex),
    "claude": ("Anthropic", "Claude Code", fetch_claude),
    "gemini": ("Google", "Gemini CLI", fetch_gemini),
}
