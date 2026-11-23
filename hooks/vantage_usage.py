#!/usr/bin/env python3
"""Cursor afterAgentResponse hook: send one OTLP log per local agent turn to Vantage.

The log carries the model, token counts, repository, pull request, and ticket.
It never reads or sends prompt or response text. Cloud Agent payloads omit
token counts, so they are skipped. The hook always prints "{}" and exits 0 so
a failed send never blocks the agent. It uses only the Python standard library.

Credentials come, in order, from the team's VANTAGE_TELEMETRY_TOKEN plugin
variable (passed in as VANTAGE_CURSOR_TEAM_TOKEN), VANTAGE_CURSOR_INGEST_TOKEN
set by device management, or the file the setup skill writes. Each send records
its credential source and result, never the token, so the setup skill can
report whether telemetry works.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None


SCHEMA_VERSION = "1"
PLUGIN_VERSION = "1.2.0"
# The ingest endpoint rejects urllib's default User-Agent.
USER_AGENT = "vantage-cursor-plugin/%s" % PLUGIN_VERSION
EVENT_NAME = "cursor.agent.turn"
DEFAULT_ENDPOINT = "https://ingest.vantage.sh"
DEFAULT_CLASSIFIER_ENDPOINT = "https://api.vantage.sh/plugins/classifier"
MAX_CLASSIFIER_BYTES = 64 * 1024
CLASSIFIER_TIMEOUT = 2
TAG_SECONDS = 604_800
MAX_CLASSIFICATION_TAGS = 32
MAX_CLASSIFICATION_VALUE_LENGTH = 512
CLASSIFICATION_TAG_KEY = re.compile(r"\A[a-z][a-z0-9_.-]{0,63}\Z")
TOKEN_FIELDS = (
    ("input_tokens", "cursor.agent.input_tokens"),
    ("output_tokens", "cursor.agent.output_tokens"),
    ("cache_read_tokens", "cursor.agent.cache_read_tokens"),
    ("cache_write_tokens", "cursor.agent.cache_write_tokens"),
)
OPEN_TIMEOUT = 3
LOOKUP_TIMEOUT = 2
LOOKUP_BUDGET = 4
LOOKUP_CACHE_SECONDS = 900
LOOKUP_FAILURE_SECONDS = 60
SENT_SECONDS = 3600
GITHUB_REPOSITORY = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
DEFAULT_TICKET = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


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


def classifier_markdown_path(env=os.environ):
    configured = env.get("VANTAGE_CURSOR_CLASSIFIER_PATH", "")
    return configured or os.path.join(os.path.dirname(config_path(env)), "classifier.md")


def team_classifier_markdown_path(env, token, endpoint):
    key = hashlib.sha256(f"{endpoint}\n{token}".encode("utf-8")).hexdigest()
    return os.path.join(os.path.dirname(config_path(env)), "team-classifier-cache", key, "classifier.md")


def classifier_endpoint(env=os.environ):
    return env.get("VANTAGE_CLASSIFIER_ENDPOINT", "").strip() or DEFAULT_CLASSIFIER_ENDPOINT


def team_token(env=os.environ, variable="VANTAGE_CURSOR_TEAM_TOKEN"):
    token = env.get(variable, "").strip()
    if not token or "${" in token or any(character.isspace() for character in token):
        return None
    return token


def credentials(env=os.environ):
    """Return (token, endpoint, config, source).

    Admin-managed tokens win over a developer's own sign-in: the team plugin
    variable first, then the device-management environment, then the file.
    """
    config = read_config(env)
    environment_endpoint = env.get("VANTAGE_CURSOR_INGEST_ENDPOINT", "").strip() or DEFAULT_ENDPOINT

    token = team_token(env)
    if token:
        return token, environment_endpoint, config, "team"

    token = env.get("VANTAGE_CURSOR_INGEST_TOKEN", "").strip()
    if token:
        return token, environment_endpoint, config, "environment"

    token = config.get("token")
    if not isinstance(token, str) or not token.strip():
        return None, None, config, None
    endpoint = config.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        endpoint = DEFAULT_ENDPOINT
    return token.strip(), endpoint.strip(), config, "file"


def record_status(env, source, result):
    def update(current):
        current.clear()
        current.update({"source": source, "result": result, "at": int(time.time())})

    update_state(env, "status.json", update)


def ticket_pattern(env, config):
    prefixes = env.get("VANTAGE_TICKET_PREFIXES")
    if prefixes is not None:
        prefixes = [prefix.strip() for prefix in prefixes.split(",")]
    else:
        prefixes = config.get("ticket_prefixes")
    if not isinstance(prefixes, list):
        return DEFAULT_TICKET
    prefixes = [prefix for prefix in prefixes if isinstance(prefix, str) and re.match(r"^[A-Z][A-Z0-9]{0,9}$", prefix)]
    if not prefixes:
        return DEFAULT_TICKET
    return re.compile(r"\b((?:%s)-\d+)\b" % "|".join(prefixes))


def string_attribute(key, value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return {"key": key, "value": {"stringValue": text}}


def int_attribute(key, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return {"key": key, "value": {"intValue": str(value)}}


def bool_string(value):
    if value is None or str(value).strip() == "":
        return None
    if value is True or str(value).strip().lower() == "true":
        return "true"
    if value is False or str(value).strip().lower() == "false":
        return "false"
    return None


def payload_value(payload, *keys):
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        return value
    return None


def param_value(payload, ids):
    params = payload.get("model_params")
    if not isinstance(params, list):
        return None
    for param in params:
        if not isinstance(param, dict) or param.get("id") not in ids:
            continue
        value = param.get("value")
        if value is not None and str(value).strip():
            return value
    return None


def setting(payload, local, *ids):
    value = param_value(payload, ids)
    if value is not None:
        return value
    for key in ids:
        candidate = local.get(key)
        if candidate is not None and str(candidate).strip():
            return candidate
    return None


def is_auto(payload):
    return any(str(payload.get(key) or "").lower().startswith("auto") for key in ("model_id", "model"))


def is_cloud_agent(payload):
    return (
        payload.get("is_background_agent") is True
        or str(payload.get("conversation_id") or "").startswith("bc-")
        or payload_value(payload, "cloud_agent_id", "cloudAgentId") is not None
    )


def token_attributes(payload):
    attributes = [int_attribute(key, payload.get(field)) for field, key in TOKEN_FIELDS]
    return [attribute for attribute in attributes if attribute]


def state_dir(env):
    configured = env.get("VANTAGE_CURSOR_USAGE_STATE_DIR", "")
    if configured:
        return configured
    return os.path.join(tempfile.gettempdir(), "vantage-cursor-usage")


def update_state(env, filename, update):
    """Apply update(dict) to a JSON state file under an exclusive lock."""
    try:
        directory = state_dir(env)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, filename)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_EX)
            raw = handle.read()
            try:
                current = json.loads(raw) if raw.strip() else {}
            except ValueError:
                current = {}
            if not isinstance(current, dict):
                current = {}
            result = update(current)
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(current))
            return result
    except OSError:
        return None


def update_tags(env, session_id, options):
    def update(current):
        now = int(time.time())
        for key in list(current):
            value = current[key]
            saved_at = value.get("at") if isinstance(value, dict) else None
            if not isinstance(saved_at, int) or not 0 <= now - saved_at <= TAG_SECONDS:
                del current[key]
        tags = {
            key: value
            for key, value in current.get(session_id, {}).items()
            if key != "at" and valid_classification_tag(key, value)
        }
        for key, value in options.items():
            add_classification_tag(tags, key, value)
        current[session_id] = dict(tags, at=now)
        return True

    return update_state(env, "tags.json", update) is True


def valid_classification_tag(key, value):
    return (
        isinstance(key, str)
        and CLASSIFICATION_TAG_KEY.fullmatch(key) is not None
        and isinstance(value, str)
        and bool(value)
        and len(value) <= MAX_CLASSIFICATION_VALUE_LENGTH
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def add_classification_tag(tags, key, value):
    key = key.strip()
    value = value.strip()
    if not valid_classification_tag(key, value):
        raise ValueError("classification tags require a lowercase key and a non-empty value")
    if key not in tags and len(tags) >= MAX_CLASSIFICATION_TAGS:
        raise ValueError("at most %d classification tags are allowed" % MAX_CLASSIFICATION_TAGS)
    tags[key] = value


def classification_attributes(tags):
    return [
        string_attribute("vantage.classification.%s" % key, value)
        for key, value in sorted(tags.items())
        if valid_classification_tag(key, value)
    ]


def skill_tags(env, session_id):
    try:
        with open(os.path.join(state_dir(env), "tags.json"), encoding="utf-8") as handle:
            current = json.load(handle)
        value = current.get(session_id, {}) if isinstance(current, dict) else {}
        if not isinstance(value, dict) or int(time.time()) - value.get("at", 0) > TAG_SECONDS:
            return {}
        return {
            key: tag_value
            for key, tag_value in value.items()
            if key != "at" and valid_classification_tag(key, tag_value)
        }
    except (OSError, TypeError, ValueError):
        return {}


def tag(argv, env=os.environ, stdout=sys.stdout, stderr=sys.stderr):
    try:
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--session")
        parser.add_argument("--tag", action="append", default=[])
        parsed = vars(parser.parse_args(argv))
        session_id = (parsed.pop("session") or "").strip()
        if not session_id:
            raise ValueError("--session is required")

        raw_tags = parsed.pop("tag")
        options = {}
        for raw_tag in raw_tags:
            key, separator, value = raw_tag.partition("=")
            if not separator:
                raise ValueError("--tag must use key=value")
            add_classification_tag(options, key, value)
        if not options:
            raise ValueError("give at least one tag")
        if not update_tags(env, session_id, options):
            raise ValueError("could not save tags")
        stdout.write("Saved Vantage tags for this Cursor session.\n")
        return 0
    except (Exception, SystemExit) as error:
        message = "invalid tag arguments" if isinstance(error, SystemExit) else str(error)
        stderr.write("[vantage_usage] %s\n" % message)
        return 1


def request_key(payload):
    return str(payload.get("generation_id") or "") or str(payload.get("conversation_id") or "")


def already_sent(env, payload):
    key = request_key(payload)
    if not key:
        return False

    def update(current):
        now = int(time.time())
        for sent_key in list(current):
            sent_at = current[sent_key]
            if not isinstance(sent_at, int) or now - sent_at > SENT_SECONDS or now < sent_at:
                del current[sent_key]
        if key in current:
            return True
        current[key] = now
        while len(current) > 100 and next(iter(current)) != key:
            del current[next(iter(current))]
        return False

    return update_state(env, "sent.json", update) is True


def remaining(deadline):
    left = deadline - time.monotonic()
    if left < 0.2:
        return None
    return min(left, LOOKUP_TIMEOUT)


def capture(argv, deadline, cwd=None, env=os.environ):
    timeout = remaining(deadline)
    if timeout is None:
        return None
    spawn_env = {"GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"}
    if env.get("PATH"):
        spawn_env["PATH"] = env["PATH"]
    if env.get("HOME"):
        spawn_env["HOME"] = env["HOME"]
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=spawn_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.decode("utf-8", "replace").strip()
    return output or None


def workspace_root(payload, env):
    roots = payload.get("workspace_roots")
    if isinstance(roots, list) and len(roots) == 1:
        return str(roots[0] or "")
    return env.get("CURSOR_PROJECT_DIR", "")


def github_repository(remote):
    match = GITHUB_REPOSITORY.search(remote or "")
    return match.group(1) if match else None


def lookup_cache(env, key):
    path = os.path.join(state_dir(env), "plugin-lookups.json")
    try:
        with open(path, encoding="utf-8") as handle:
            entry = json.load(handle).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("checked_at"), int):
        return None
    age = int(time.time()) - entry["checked_at"]
    limit = LOOKUP_CACHE_SECONDS if entry.get("ok") is True else LOOKUP_FAILURE_SECONDS
    if age < 0 or age >= limit:
        return None
    return entry


def write_lookup(env, key, pull):
    def update(current):
        current.pop(key, None)
        current[key] = {
            "ok": pull.get("ok") is True,
            "ticket": pull.get("ticket"),
            "github": pull.get("github"),
            "checked_at": int(time.time()),
        }
        while len(current) > 20 and next(iter(current)) != key:
            del current[next(iter(current))]

    update_state(env, "plugin-lookups.json", update)


def pull_request(root, deadline, pattern, env):
    output = capture(["gh", "pr", "view", "--json", "title,url"], deadline, cwd=root, env=env)
    if output is None:
        return {"ok": False}
    try:
        parsed = json.loads(output)
    except ValueError:
        return {"ok": False}
    if not isinstance(parsed, dict):
        return {"ok": False}
    result = {"ok": True}
    match = pattern.search(str(parsed.get("title") or ""))
    if match:
        result["ticket"] = match.group(1)
    url = parsed.get("url")
    if isinstance(url, str) and url.strip():
        result["github"] = url.strip()
    return result


def workspace_context(root, deadline, pattern, env):
    """Return the GitHub owner/name and open pull request for the workspace.

    The origin URL can embed a credential, so only the parsed owner/name is
    kept or written to the lookup cache.
    """
    if not root:
        return None, {}
    if not os.path.isdir(root):
        return os.path.basename(root.rstrip("/\\")), {}

    remote = capture(["git", "-C", root, "remote", "get-url", "origin"], deadline, env=env)
    repository = github_repository(remote)
    if repository is None:
        return os.path.basename(root.rstrip("/\\")), {}

    branch = capture(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"], deadline, env=env) or ""
    key = "\n".join([root, branch, repository, pattern.pattern])
    cached = lookup_cache(env, key)
    if cached is not None:
        return repository, cached

    pull = pull_request(root, deadline, pattern, env)
    write_lookup(env, key, pull)
    return repository, pull


def cursor_state_database(env):
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    elif os.name == "nt":
        base = env.get("APPDATA", "")
    else:
        base = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, "Cursor", "User", "globalStorage", "state.vscdb")


def local_model_context(conversation_id, env):
    """Read max mode and model parameters missing from the payload from Cursor's local state."""
    context = {}
    database = cursor_state_database(env)
    if not conversation_id or not os.path.exists(database):
        return context
    try:
        uri = "file:%s?mode=ro" % urllib.parse.quote(database)
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            row = connection.execute(
                "SELECT json_extract(value, '$.modelConfig') FROM cursorDiskKV WHERE key = ?",
                ("composerData:%s" % conversation_id,),
            ).fetchone()
        finally:
            connection.close()
        config = json.loads(row[0]) if row and row[0] else None
    except (sqlite3.Error, ValueError, TypeError):
        return context
    if not isinstance(config, dict):
        return context

    if isinstance(config.get("maxMode"), bool):
        context["max_mode"] = config["maxMode"]
    selected = config.get("selectedModels")
    selected = selected[0] if isinstance(selected, list) and selected else None
    if isinstance(selected, dict):
        for param in selected.get("parameters") or []:
            if isinstance(param, dict) and param.get("id") not in context and param.get("value") is not None:
                context[param["id"]] = param["value"]
    return context


def build_request(payload, env=os.environ, config=None, now=None):
    """Return the OTLP/HTTP JSON body, or None when this reply has no token counts."""
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "afterAgentResponse":
        return None
    tokens = token_attributes(payload)
    if not tokens or already_sent(env, payload):
        return None

    config = config or {}
    conversation_id = str(payload.get("conversation_id") or "")
    classified = {}
    if not team_token(env) or team_token(env, "VANTAGE_CURSOR_TEAM_CLASSIFIER_TOKEN"):
        classified = skill_tags(env, conversation_id)
    deadline = time.monotonic() + LOOKUP_BUDGET
    repository, pull = workspace_context(workspace_root(payload, env), deadline, ticket_pattern(env, config), env)
    local = local_model_context(conversation_id, env)
    timestamp = str(int((now if now is not None else time.time()) * 1_000_000_000))
    text = payload.get("text")

    resource = [
        string_attribute("service.name", "cursor"),
        string_attribute("cursor.surface", "local"),
        string_attribute("cursor.version", payload.get("cursor_version") or env.get("CURSOR_VERSION")),
        string_attribute("cursor.user.email", payload.get("user_email") or env.get("CURSOR_USER_EMAIL")),
        string_attribute("vcs.repository.name", repository),
        string_attribute("vantage.source", "cursor_hook"),
        string_attribute("vantage.schema_version", SCHEMA_VERSION),
        string_attribute("vantage.plugin.version", PLUGIN_VERSION),
    ]
    attributes = [
        string_attribute("event.name", EVENT_NAME),
        string_attribute("vantage.event_id", str(uuid.uuid4())),
        string_attribute("cursor.conversation.id", conversation_id),
        string_attribute("cursor.generation.id", payload.get("generation_id")),
        string_attribute("cursor.model.name", payload.get("model")),
        string_attribute("cursor.model.id", payload.get("model_id")),
        string_attribute("cursor.agent.status", payload.get("status")),
        string_attribute("cursor.agent.auto", "true" if is_auto(payload) else None),
        string_attribute("cursor.agent.optimize_for", setting(payload, local, "optimize_for")),
        string_attribute("cursor.agent.max_mode", bool_string(setting(payload, local, "max_mode", "max", "maxMode"))),
        string_attribute("cursor.agent.effort", setting(payload, local, "effort", "reasoning", "reasoning_effort")),
        string_attribute("cursor.agent.fast", bool_string(setting(payload, local, "fast"))),
        string_attribute("cursor.agent.context_window", setting(payload, local, "context", "context_window")),
        string_attribute("vantage.ticket", pull.get("ticket")),
        string_attribute("vantage.github", pull.get("github")),
        string_attribute("cursor.agent.automation_id", payload_value(payload, "automation_id", "automationId")),
        string_attribute("cursor.agent.headless", payload_value(payload, "headless", "is_headless", "isHeadless")),
        int_attribute("cursor.agent.response_characters", len(text) if isinstance(text, str) else None),
    ]

    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [item for item in resource if item]},
                "scopeLogs": [
                    {
                        "scope": {"name": "vantage.cursor_hooks", "version": SCHEMA_VERSION},
                        "logRecords": [
                            {
                                "timeUnixNano": timestamp,
                                "observedTimeUnixNano": timestamp,
                                "severityNumber": 9,
                                "severityText": "INFO",
                                "eventName": EVENT_NAME,
                                "body": {"stringValue": EVENT_NAME},
                                "attributes": [item for item in attributes if item]
                                + classification_attributes(classified)
                                + tokens,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def logs_url(endpoint):
    url = urllib.parse.urljoin(endpoint, "/v1/logs")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS):
        return url
    raise ValueError("refusing to send the ingest token over %s" % (parsed.scheme or "an unknown scheme"))


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def write_private_file(path, content):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".%s." % os.path.basename(path))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def classifier_settings(env):
    endpoint = classifier_endpoint(env)
    if team_token(env):
        token = team_token(env, "VANTAGE_CURSOR_TEAM_CLASSIFIER_TOKEN")
        if not token:
            return None
        markdown_path = team_classifier_markdown_path(env, token, endpoint)
    else:
        markdown_path = classifier_markdown_path(env)
        token = read_config(env).get("classifier_token")
        if not isinstance(token, str) or not token.strip():
            return None
    return endpoint, token.strip(), markdown_path


def refresh_classifier(settings, stderr=sys.stderr):
    endpoint, token, markdown_path = settings
    etag_path = markdown_path + ".etag"
    cached_path = markdown_path if os.path.isfile(markdown_path) else None
    try:
        parsed = urllib.parse.urlparse(endpoint)
        if not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS)
        ):
            raise ValueError("refusing to send the classifier credential over %s" % parsed.scheme)

        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % token,
            "User-Agent": USER_AGENT,
        }
        try:
            with open(etag_path, encoding="utf-8") as handle:
                etag = handle.read().strip()
            if etag and cached_path:
                headers["If-None-Match"] = etag
        except (OSError, UnicodeError):
            pass

        request = urllib.request.Request(endpoint, headers=headers, method="GET")
        opener = urllib.request.build_opener(NoRedirectHandler)
        try:
            response = opener.open(request, timeout=CLASSIFIER_TIMEOUT)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 304:
                return cached_path
            if error.code in (401, 403):
                for path in (markdown_path, etag_path):
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
                return None
            raise
        with response:
            raw = response.read(MAX_CLASSIFIER_BYTES + 1)
            if len(raw) > MAX_CLASSIFIER_BYTES:
                raise ValueError("classifier response is too large")
            payload = json.loads(raw.decode("utf-8"))
            markdown = payload.get("classifier_markdown") if isinstance(payload, dict) else None
            if not isinstance(markdown, str) or not markdown.strip():
                raise ValueError("classifier response is empty")
            write_private_file(markdown_path, markdown.encode("utf-8"))
            new_etag = response.headers.get("ETag", "").strip()
            if new_etag:
                write_private_file(etag_path, new_etag.encode("utf-8"))
        return markdown_path
    except (OSError, UnicodeError, ValueError, urllib.error.URLError) as error:
        stderr.write("[vantage_usage] classifier refresh failed: %s\n" % type(error).__name__)
        return cached_path


def attribution_context(session_id, markdown_path):
    script = os.path.abspath(__file__)
    arguments = [sys.executable, script, "tag", "--session", session_id]
    if os.name == "nt":
        command = subprocess.list2cmdline(arguments)
    else:
        command = " ".join(shlex.quote(argument) for argument in arguments)
    return (
        "Vantage classifier file for this Cursor session: %s\n"
        "Vantage session attribution command: %s\n"
        "Use the vantage-session-attribution skill to read the classifier and append only the flags it directs."
        % (markdown_path, command)
    )


def session_start(payload, env=os.environ, stderr=sys.stderr):
    session_id = str(payload.get("session_id") or payload.get("conversation_id") or "").strip()
    if not session_id:
        return None
    settings = classifier_settings(env)
    if settings is None:
        return None

    _, _, markdown_path = settings
    if os.path.isfile(markdown_path):
        try:
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--refresh-classifier"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            stderr.write("[vantage_usage] could not start classifier refresh: %s\n" % type(error).__name__)
        return attribution_context(session_id, markdown_path)

    markdown_path = refresh_classifier(settings, stderr=stderr)
    if markdown_path:
        return attribution_context(session_id, markdown_path)
    return None


def deliver(body, token, endpoint):
    request = urllib.request.Request(
        logs_url(endpoint),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=OPEN_TIMEOUT) as response:
        return response.status


def handle(payload, env=os.environ, stderr=sys.stderr):
    if not isinstance(payload, dict) or is_cloud_agent(payload):
        return None
    token, endpoint, config, source = credentials(env)
    if not token:
        return None
    body = build_request(payload, env=env, config=config)
    if body is None:
        return None
    try:
        status = deliver(body, token, endpoint)
        record_status(env, source, status)
        return status
    except urllib.error.HTTPError as error:
        record_status(env, source, error.code)
        stderr.write("[vantage_usage] ingest responded %s\n" % error.code)
    except (OSError, ValueError) as error:
        record_status(env, source, "unreachable")
        stderr.write("[vantage_usage] %s: %s\n" % (type(error).__name__, error))
    return None


def main(argv=(), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=os.environ):
    if argv and argv[0] == "tag":
        return tag(argv[1:], env=env, stdout=stdout, stderr=stderr)
    if argv == ["--refresh-classifier"]:
        settings = classifier_settings(env)
        if settings is not None:
            refresh_classifier(settings, stderr=stderr)
        return 0
    response = {}
    try:
        payload = json.loads(stdin.read() or "null")
        if isinstance(payload, dict) and payload.get("hook_event_name") == "sessionStart":
            context = session_start(payload, env=env, stderr=stderr)
            if context:
                response["additional_context"] = context
        else:
            handle(payload, env=env, stderr=stderr)
    except ValueError:
        pass
    except Exception as error:  # noqa: BLE001 - a hook must never fail the agent turn
        stderr.write("[vantage_usage] %s: %s\n" % (type(error).__name__, error))
    finally:
        stdout.write(json.dumps(response) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
