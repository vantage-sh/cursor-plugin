import io
import json
import os
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT, FakeServer, load

hook = load("vantage_usage", "hooks/vantage_usage.py")


def payload(**overrides):
    base = {
        "hook_event_name": "afterAgentResponse",
        "conversation_id": "conv-1",
        "generation_id": "gen-1",
        "model": "claude-opus",
        "model_id": "claude-opus-5",
        "cursor_version": "3.1.0",
        "user_email": "dev@example.com",
        "workspace_roots": [],
        "text": "secret response text",
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_tokens": 900,
        "cache_write_tokens": 50,
        "model_params": [{"id": "effort", "value": "high"}],
    }
    base.update(overrides)
    return base


def attributes(items):
    result = {}
    for item in items:
        value = item["value"]
        result[item["key"]] = value.get("stringValue", value.get("intValue"))
    return result


class HookTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            "VANTAGE_CURSOR_USAGE_STATE_DIR": os.path.join(self.tmp.name, "state"),
            "VANTAGE_CURSOR_TELEMETRY_CONFIG": os.path.join(self.tmp.name, "config.json"),
            "HOME": self.tmp.name,
            "PATH": os.environ.get("PATH", ""),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, config):
        with open(self.env["VANTAGE_CURSOR_TELEMETRY_CONFIG"], "w", encoding="utf-8") as handle:
            json.dump(config, handle)


class BuildRequestTest(HookTestCase):
    def test_builds_core_compatible_log_without_response_text(self):
        body = hook.build_request(payload(), env=self.env, now=1_700_000_000)

        resource_logs = body["resourceLogs"][0]
        resource = attributes(resource_logs["resource"]["attributes"])
        scope_logs = resource_logs["scopeLogs"][0]
        record = scope_logs["logRecords"][0]
        record_attributes = attributes(record["attributes"])

        self.assertEqual(resource["service.name"], "cursor")
        self.assertEqual(resource["cursor.surface"], "local")
        self.assertEqual(resource["cursor.user.email"], "dev@example.com")
        self.assertEqual(resource["vantage.source"], "cursor_hook")
        self.assertEqual(resource["vantage.plugin.version"], hook.PLUGIN_VERSION)
        self.assertEqual(scope_logs["scope"], {"name": "vantage.cursor_hooks", "version": "1"})
        self.assertEqual(record["eventName"], "cursor.agent.turn")
        self.assertEqual(record["timeUnixNano"], "1700000000000000000")
        self.assertEqual(record_attributes["cursor.generation.id"], "gen-1")
        self.assertEqual(record_attributes["cursor.model.name"], "claude-opus")
        self.assertEqual(record_attributes["cursor.agent.effort"], "high")
        self.assertEqual(record_attributes["cursor.agent.input_tokens"], "1200")
        self.assertEqual(record_attributes["cursor.agent.output_tokens"], "300")
        self.assertEqual(record_attributes["cursor.agent.cache_read_tokens"], "900")
        self.assertEqual(record_attributes["cursor.agent.cache_write_tokens"], "50")
        self.assertEqual(record_attributes["cursor.agent.response_characters"], str(len("secret response text")))
        self.assertNotIn("secret response text", json.dumps(body))

    def test_skips_replies_without_token_counts(self):
        reply = payload()
        for field, _key in hook.TOKEN_FIELDS:
            reply.pop(field)

        self.assertIsNone(hook.build_request(reply, env=self.env))

    def test_reports_each_generation_once(self):
        self.assertIsNotNone(hook.build_request(payload(), env=self.env))
        self.assertIsNone(hook.build_request(payload(), env=self.env))
        self.assertIsNotNone(hook.build_request(payload(generation_id="gen-2"), env=self.env))

    def test_respects_generations_recorded_by_the_core_project_hook(self):
        os.makedirs(self.env["VANTAGE_CURSOR_USAGE_STATE_DIR"])
        with open(os.path.join(self.env["VANTAGE_CURSOR_USAGE_STATE_DIR"], "sent.json"), "w") as handle:
            json.dump({"gen-1": int(__import__("time").time())}, handle)

        self.assertIsNone(hook.build_request(payload(), env=self.env))

    def test_uses_repository_name_and_ticket_from_pull_request(self):
        repo = os.path.join(self.tmp.name, "repo")
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(
            ["git", "-C", repo, "remote", "add", "origin", "https://user:secret@github.com/acme/widgets.git"],
            check=True,
        )
        bin_dir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, "gh"), "w") as handle:
            handle.write('#!/bin/sh\necho \'{"title":"ENG-42 Fix it","url":"https://github.com/acme/widgets/pull/7"}\'\n')
        os.chmod(os.path.join(bin_dir, "gh"), 0o755)
        env = dict(self.env, PATH=bin_dir + os.pathsep + self.env["PATH"], VANTAGE_TICKET_PREFIXES="ENG")

        body = hook.build_request(payload(workspace_roots=[repo]), env=env)
        resource = attributes(body["resourceLogs"][0]["resource"]["attributes"])
        record_attributes = attributes(body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"])

        self.assertEqual(resource["vcs.repository.name"], "acme/widgets")
        self.assertEqual(record_attributes["vantage.ticket"], "ENG-42")
        self.assertEqual(record_attributes["vantage.github"], "https://github.com/acme/widgets/pull/7")
        self.assertNotIn("secret", json.dumps(body))

    def test_ticket_prefixes_limit_matches(self):
        pattern = hook.ticket_pattern({}, {"ticket_prefixes": ["OPS"]})

        self.assertIsNone(pattern.search("ENG-1 change"))
        self.assertEqual(pattern.search("OPS-9 change").group(1), "OPS-9")
        self.assertEqual(hook.ticket_pattern({}, {}).search("ENG-1 change").group(1), "ENG-1")


class CredentialsTest(HookTestCase):
    def test_team_plugin_token_wins_over_environment_and_file(self):
        self.write_config({"token": "file-token"})
        env = dict(self.env, VANTAGE_CURSOR_TEAM_TOKEN="team-token", VANTAGE_CURSOR_INGEST_TOKEN="env-token")

        token, endpoint, _config, source = hook.credentials(env)

        self.assertEqual((token, endpoint, source), ("team-token", hook.DEFAULT_ENDPOINT, "team"))

    def test_ignores_unset_team_variable(self):
        self.write_config({"token": "file-token"})

        for value in ("", "   ", "${VANTAGE_TELEMETRY_TOKEN}", "${user_config.VANTAGE_TELEMETRY_TOKEN}"):
            env = dict(self.env, VANTAGE_CURSOR_TEAM_TOKEN=value)
            self.assertEqual(hook.credentials(env)[3], "file", value)

    def test_environment_wins_over_saved_file(self):
        self.write_config({"token": "file-token", "endpoint": "https://file.example"})
        env = dict(self.env, VANTAGE_CURSOR_INGEST_TOKEN="env-token")

        self.assertEqual(hook.credentials(env)[:2], ("env-token", hook.DEFAULT_ENDPOINT))

    def test_reads_saved_file(self):
        self.write_config({"token": "file-token", "endpoint": "https://file.example"})

        self.assertEqual(hook.credentials(self.env)[:2], ("file-token", "https://file.example"))

    def test_without_credentials_returns_none(self):
        self.assertEqual(hook.credentials(self.env)[:2], (None, None))

    def test_refuses_plain_http_to_remote_hosts(self):
        with self.assertRaises(ValueError):
            hook.logs_url("http://ingest.example.com")
        self.assertEqual(hook.logs_url("http://127.0.0.1:9000"), "http://127.0.0.1:9000/v1/logs")


class MainTest(HookTestCase):
    def run_main(self, reply, env):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = hook.main(stdin=io.StringIO(json.dumps(reply)), stdout=stdout, stderr=stderr, env=env)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_sends_log_with_bearer_token(self):
        with FakeServer() as server:
            self.write_config({"token": "vntg_ti_test", "endpoint": server.url})
            code, stdout, _stderr = self.run_main(payload(), self.env)

        self.assertEqual((code, stdout), (0, "{}\n"))
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(server.requests[0]["path"], "/v1/logs")
        self.assertEqual(server.requests[0]["headers"]["Authorization"], "Bearer vntg_ti_test")

    def test_skips_cloud_agents(self):
        with FakeServer() as server:
            self.write_config({"token": "vntg_ti_test", "endpoint": server.url})
            self.run_main(payload(conversation_id="bc-123"), self.env)
            self.run_main(payload(generation_id="gen-2", is_background_agent=True), self.env)

        self.assertEqual(server.requests, [])

    def test_does_nothing_without_credentials(self):
        with FakeServer() as server:
            code, stdout, _stderr = self.run_main(payload(), self.env)

        self.assertEqual((code, stdout), (0, "{}\n"))
        self.assertEqual(server.requests, [])

    def test_ingest_errors_never_fail_the_hook(self):
        with FakeServer(default=(401, {})) as server:
            self.write_config({"token": "revoked", "endpoint": server.url})
            code, stdout, stderr = self.run_main(payload(), self.env)

        self.assertEqual((code, stdout), (0, "{}\n"))
        self.assertIn("ingest responded 401", stderr)

    def test_invalid_input_still_answers_cursor(self):
        stdout = io.StringIO()

        self.assertEqual(hook.main(stdin=io.StringIO("not json"), stdout=stdout, stderr=io.StringIO(), env=self.env), 0)
        self.assertEqual(stdout.getvalue(), "{}\n")

    def test_records_source_and_result_without_the_token(self):
        with FakeServer(default=(401, {})) as server:
            env = dict(self.env, VANTAGE_CURSOR_TEAM_TOKEN="vntg_ti_team", VANTAGE_CURSOR_INGEST_ENDPOINT=server.url)
            self.run_main(payload(), env)

        with open(os.path.join(self.env["VANTAGE_CURSOR_USAGE_STATE_DIR"], "status.json")) as handle:
            recorded = json.load(handle)

        self.assertEqual((recorded["source"], recorded["result"]), ("team", 401))
        self.assertNotIn("vntg_ti_team", json.dumps(recorded))

    def run_hook_command(self, env):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as handle:
            command = json.load(handle)["hooks"]["afterAgentResponse"][0]["command"]
        return subprocess.run(
            ["sh", "-c", command],
            cwd=ROOT,
            input=json.dumps(payload()).encode("utf-8"),
            env=env,
            capture_output=True,
            check=True,
        )

    def test_hook_command_passes_the_team_variable_to_the_hook(self):
        with FakeServer() as server:
            env = dict(self.env, VANTAGE_TELEMETRY_TOKEN="vntg_ti_team", VANTAGE_CURSOR_INGEST_ENDPOINT=server.url)
            completed = self.run_hook_command(env)

        self.assertEqual(completed.stdout, b"{}\n")
        self.assertEqual(server.requests[0]["headers"]["Authorization"], "Bearer vntg_ti_team")

    def test_hook_command_falls_back_when_the_team_variable_is_unset(self):
        with FakeServer() as server:
            self.write_config({"token": "vntg_ti_personal", "endpoint": server.url})
            completed = self.run_hook_command(self.env)

        self.assertEqual(completed.stdout, b"{}\n")
        self.assertEqual(server.requests[0]["headers"]["Authorization"], "Bearer vntg_ti_personal")

    def test_shell_wrapper_answers_cursor(self):
        completed = subprocess.run(
            ["sh", os.path.join(ROOT, "hooks", "run_vantage_usage.sh")],
            input=json.dumps(payload()).encode("utf-8"),
            env=self.env,
            capture_output=True,
            check=True,
        )

        self.assertEqual(completed.stdout, b"{}\n")


if __name__ == "__main__":
    unittest.main()
