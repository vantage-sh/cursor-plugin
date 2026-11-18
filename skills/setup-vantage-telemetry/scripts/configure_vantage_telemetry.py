#!/usr/bin/env python3
"""Connect this computer's Cursor agent usage to Vantage, check it, or remove it.

Sign-in opens Vantage in the browser. After approval, the script receives an
ingest token, checks that Vantage accepts it, and saves it where the usage
hook reads it. The token is never printed.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

DEFAULT_AUTHORIZATION_ENDPOINT = "https://console.vantage.sh/cursor/telemetry/authorize"
DEFAULT_CLAIM_ENDPOINT = "https://api.vantage.sh/plugins/cursor_telemetry_setup/claim"
DEFAULT_INGEST_ENDPOINT = "https://ingest.vantage.sh"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
SIGN_IN_TIMEOUT = 600
REQUEST_TIMEOUT = 15


class SetupError(Exception):
    pass


def config_path(env=os.environ):
    configured = env.get("VANTAGE_CURSOR_TELEMETRY_CONFIG", "")
    if configured:
        return configured
    if os.name == "nt" and env.get("APPDATA"):
        base = env["APPDATA"]
    else:
        base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "vantage", "cursor-telemetry.json")


def read_config(env=os.environ):
    try:
        with open(config_path(env), encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def write_config(config, env=os.environ):
    path = config_path(env)
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".cursor-telemetry.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return path


def secure_url(url, purpose):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS):
        return url
    raise SetupError("The %s must use https: %s" % (purpose, url))


def endpoints(env=os.environ):
    return {
        "authorization": secure_url(
            env.get("VANTAGE_CURSOR_AUTHORIZATION_ENDPOINT") or DEFAULT_AUTHORIZATION_ENDPOINT,
            "Vantage sign-in address",
        ),
        "claim": secure_url(env.get("VANTAGE_CURSOR_CLAIM_ENDPOINT") or DEFAULT_CLAIM_ENDPOINT, "Vantage API address"),
        "ingest": secure_url(
            env.get("VANTAGE_CURSOR_INGEST_ENDPOINT") or DEFAULT_INGEST_ENDPOINT,
            "Vantage telemetry address",
        ),
    }


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_url(base, challenge):
    query = urllib.parse.urlencode({"code_challenge": challenge, "code_challenge_method": "S256"})
    separator = "&" if "?" in base else "?"
    return base + separator + query


def post_json(url, body, headers=None, timeout=REQUEST_TIMEOUT):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=dict({"Content-Type": "application/json", "Accept": "application/json"}, **(headers or {})),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read()
        status = error.code
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def wait_for_ingest_token(claim_url, verifier, timeout=SIGN_IN_TIMEOUT, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    delay = 2.0
    while True:
        try:
            status, body = post_json(claim_url, {"code_verifier": verifier})
        except (OSError, ValueError):
            status, body = None, {}

        if status == 201:
            token = body.get("plaintext_token")
            if isinstance(token, str) and token.strip():
                return token.strip()
            raise SetupError("Vantage approved the sign-in but returned no token. Run setup again.")
        if status == 403:
            raise SetupError("The Vantage sign-in was denied. Ask a Vantage admin for integration settings access.")
        if status == 400:
            raise SetupError("This sign-in link is no longer valid. Run setup again to get a new one.")
        if status not in (None, 202, 429) and status < 500:
            raise SetupError("Vantage responded %s while finishing sign-in." % status)

        if clock() + delay > deadline:
            raise SetupError("Timed out waiting for the Vantage sign-in. Run setup again when ready.")
        sleep(delay)
        delay = min(delay * 1.5, 10.0)


def verify_ingest_token(token, ingest_endpoint):
    url = secure_url(urllib.parse.urljoin(ingest_endpoint, "/v1/logs"), "Vantage telemetry address")
    try:
        status, _body = post_json(url, {"resourceLogs": []}, headers={"Authorization": "Bearer %s" % token})
    except (OSError, ValueError) as error:
        return False, "Could not reach Vantage: %s" % error
    if 200 <= status < 300:
        return True, "Vantage accepted the telemetry token."
    if status in (401, 403):
        return False, "Vantage rejected the telemetry token. It may have been revoked."
    return False, "Vantage responded %s to a test request." % status


def active_credentials(env=os.environ):
    token = env.get("VANTAGE_CURSOR_INGEST_TOKEN", "").strip()
    if token:
        return token, env.get("VANTAGE_CURSOR_INGEST_ENDPOINT", "").strip() or DEFAULT_INGEST_ENDPOINT, "environment"
    config = read_config(env)
    token = config.get("token")
    if isinstance(token, str) and token.strip():
        endpoint = config.get("endpoint") if isinstance(config.get("endpoint"), str) else DEFAULT_INGEST_ENDPOINT
        return token.strip(), endpoint or DEFAULT_INGEST_ENDPOINT, config_path(env)
    return None, None, None


def masked(token):
    return "ending in %s" % token[-4:] if len(token) > 8 else "(hidden)"


def hook_status(env=os.environ):
    """Return the usage hook's last send ({source, result, at}), or None."""
    directory = env.get("VANTAGE_CURSOR_USAGE_STATE_DIR") or os.path.join(tempfile.gettempdir(), "vantage-cursor-usage")
    try:
        with open(os.path.join(directory, "status.json"), encoding="utf-8") as handle:
            recorded = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(recorded, dict) or not isinstance(recorded.get("at"), int):
        return None
    return recorded


def accepted(recorded):
    return isinstance(recorded.get("result"), int) and 200 <= recorded["result"] < 300


def describe_hook_status(recorded, now=None):
    minutes = max(0, int(((now if now is not None else time.time()) - recorded["at"]) // 60))
    when = "just now" if minutes < 1 else "%d minute%s ago" % (minutes, "" if minutes == 1 else "s")
    result = recorded.get("result")
    if accepted(recorded):
        return "The last agent reply was sent %s and Vantage accepted it." % when
    if result in (401, 403):
        return "The last agent reply was sent %s and Vantage rejected the token. It may have been revoked." % when
    if result == "unreachable":
        return "The last agent reply was sent %s but Vantage could not be reached." % when
    return "The last agent reply was sent %s and Vantage responded %s." % (when, result)


def team_configured(env=os.environ):
    recorded = hook_status(env)
    return recorded if recorded and recorded.get("source") == "team" else None


def status(env=os.environ, out=sys.stdout):
    token, endpoint, source = active_credentials(env)
    if not token:
        recorded = team_configured(env)
        if recorded:
            out.write("Your Cursor team admin set a Vantage telemetry token for the Vantage plugin.\n")
            out.write(describe_hook_status(recorded) + "\n")
            return 0 if accepted(recorded) else 1
        out.write("Vantage telemetry is not set up on this computer. If your Cursor team admin set a Vantage ")
        out.write("telemetry token for the plugin, send one agent message and check again. ")
        out.write("Otherwise, ask the agent to set up Vantage telemetry.\n")
        return 1
    out.write("Telemetry token %s, from %s.\n" % (masked(token), source))
    out.write("Sending to %s.\n" % endpoint)
    ok, message = verify_ingest_token(token, endpoint)
    out.write(message + "\n")
    if ok:
        out.write("Each local agent reply sends its model, token counts, repository, pull request, and ticket. ")
        out.write("No prompt or response text is sent.\n")
    return 0 if ok else 1


def remove(env=os.environ, out=sys.stdout):
    path = config_path(env)
    if os.path.exists(path):
        os.unlink(path)
        out.write("Removed the saved Vantage telemetry token from %s.\n" % path)
        out.write("Revoke it in Vantage under Settings > Integrations > Cursor Telemetry if it is no longer needed.\n")
    else:
        out.write("No saved Vantage telemetry token was found.\n")
    if team_configured(env):
        out.write("Your Cursor team admin set a Vantage telemetry token for the plugin, so telemetry keeps sending. ")
        out.write("Your admin manages that in the Cursor dashboard.\n")
    if env.get("VANTAGE_CURSOR_INGEST_TOKEN", "").strip():
        out.write("VANTAGE_CURSOR_INGEST_TOKEN is still set in the environment, so telemetry keeps sending. ")
        out.write("Your administrator manages that setting.\n")
    return 0


def setup(env=os.environ, out=sys.stdout, force=False, open_browser=webbrowser.open, wait=wait_for_ingest_token):
    token, _endpoint, source = active_credentials(env)
    if token and not force:
        out.write("Vantage telemetry is already set up (from %s). Checking it now.\n" % source)
        return status(env, out)
    recorded = team_configured(env)
    if recorded and accepted(recorded) and not force:
        out.write("Your Cursor team admin already set a Vantage telemetry token for the plugin, ")
        out.write("so no sign-in is needed.\n")
        out.write(describe_hook_status(recorded) + "\n")
        return 0

    urls = endpoints(env)
    verifier, challenge = pkce_pair()
    url = authorization_url(urls["authorization"], challenge)
    out.write("Approve the connection in Vantage: %s\n" % url)
    out.write("Waiting up to %d minutes for approval...\n" % (SIGN_IN_TIMEOUT // 60))
    out.flush()
    try:
        open_browser(url)
    except Exception:  # noqa: BLE001 - the printed link still works without a browser
        pass

    token = wait(urls["claim"], verifier)
    ok, message = verify_ingest_token(token, urls["ingest"])
    if not ok:
        raise SetupError(message)

    path = write_config({"token": token, "endpoint": urls["ingest"]}, env)
    out.write("%s Saved it to %s (readable only by you).\n" % (message, path))
    out.write("Cursor now sends each local agent reply's model, token counts, repository, pull request, and ticket ")
    out.write("to Vantage AI Reports. No prompt or response text is sent.\n")
    return 0


def main(argv=None, env=os.environ, out=sys.stdout, err=sys.stderr):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", "--check", action="store_true", help="check the saved token")
    group.add_argument("--remove", action="store_true", help="delete the saved token")
    parser.add_argument("--force", action="store_true", help="sign in again even if a token is saved")
    args = parser.parse_args(argv)
    try:
        if args.status:
            return status(env, out)
        if args.remove:
            return remove(env, out)
        return setup(env, out, force=args.force)
    except SetupError as error:
        err.write(str(error) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
