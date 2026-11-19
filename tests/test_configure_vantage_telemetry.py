import base64
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.parse

from tests.helpers import FakeServer, load

configure = load("configure_vantage_telemetry", "skills/setup-vantage-telemetry/scripts/configure_vantage_telemetry.py")
hook = load("vantage_usage_for_configure", "hooks/vantage_usage.py")


class ConfigureTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            "VANTAGE_CURSOR_TELEMETRY_CONFIG": os.path.join(self.tmp.name, "vantage", "cursor-telemetry.json"),
            "VANTAGE_CURSOR_USAGE_STATE_DIR": os.path.join(self.tmp.name, "state"),
        }

    def tearDown(self):
        self.tmp.cleanup()


class SetupTest(ConfigureTestCase):
    def test_signs_in_verifies_and_saves_token_for_the_hook(self):
        opened = []
        with FakeServer(responses=[(202, {}), (201, {"plaintext_token": "vntg_ti_new"}), (200, {})]) as server:
            env = dict(
                self.env,
                VANTAGE_CURSOR_AUTHORIZATION_ENDPOINT=server.url + "/cursor/telemetry/authorize",
                VANTAGE_CURSOR_CLAIM_ENDPOINT=server.url + "/plugins/cursor_telemetry_setup/claim",
                VANTAGE_CURSOR_INGEST_ENDPOINT=server.url,
            )
            out = io.StringIO()
            wait = lambda url, verifier: configure.wait_for_ingest_token(url, verifier, sleep=lambda _s: None)

            code = configure.setup(env, out, open_browser=opened.append, wait=wait)

        self.assertEqual(code, 0)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(opened[0]).query)
        verifier = server.requests[0]["body"]["code_verifier"]
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual([request["path"] for request in server.requests][-1], "/v1/logs")
        self.assertEqual(server.requests[-1]["headers"]["Authorization"], "Bearer vntg_ti_new")
        self.assertEqual(server.requests[-1]["headers"]["User-Agent"], hook.USER_AGENT)
        self.assertEqual(server.requests[-1]["body"], {"resourceLogs": []})

        path = self.env["VANTAGE_CURSOR_TELEMETRY_CONFIG"]
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(hook.credentials(self.env)[:2], ("vntg_ti_new", server.url))
        self.assertNotIn("vntg_ti_new", out.getvalue())

    def test_existing_token_is_checked_instead_of_signing_in_again(self):
        with FakeServer() as server:
            configure.write_config({"token": "vntg_ti_saved", "endpoint": server.url}, self.env)
            out = io.StringIO()

            code = configure.setup(self.env, out, open_browser=self.fail)

        self.assertEqual(code, 0)
        self.assertIn("already set up", out.getvalue())
        self.assertNotIn("vntg_ti_saved", out.getvalue())

    def test_refuses_plain_http_to_remote_hosts(self):
        env = dict(self.env, VANTAGE_CURSOR_CLAIM_ENDPOINT="http://api.example.com/claim")

        with self.assertRaises(configure.SetupError):
            configure.endpoints(env)


class WaitForTokenTest(ConfigureTestCase):
    def wait(self, responses):
        with FakeServer(responses=responses) as server:
            return configure.wait_for_ingest_token(server.url, "verifier", sleep=lambda _s: None)

    def test_retries_while_pending_or_throttled(self):
        self.assertEqual(self.wait([(202, {}), (429, {}), (201, {"plaintext_token": "tok"})]), "tok")

    def test_denied_and_invalid_codes_stop(self):
        with self.assertRaisesRegex(configure.SetupError, "denied"):
            self.wait([(403, {})])
        with self.assertRaisesRegex(configure.SetupError, "no longer valid"):
            self.wait([(400, {})])

    def test_times_out(self):
        times = iter([0, 0, 700])
        with FakeServer(default=(202, {})) as server:
            with self.assertRaisesRegex(configure.SetupError, "Timed out"):
                configure.wait_for_ingest_token(server.url, "v", sleep=lambda _s: None, clock=lambda: next(times))


class TeamTokenTest(ConfigureTestCase):
    def record(self, result, source="team"):
        os.makedirs(self.env["VANTAGE_CURSOR_USAGE_STATE_DIR"], exist_ok=True)
        with open(os.path.join(self.env["VANTAGE_CURSOR_USAGE_STATE_DIR"], "status.json"), "w") as handle:
            json.dump({"source": source, "result": result, "at": int(__import__("time").time())}, handle)

    def test_status_reports_a_working_team_token(self):
        self.record(200)
        out = io.StringIO()

        self.assertEqual(configure.status(self.env, out), 0)
        self.assertIn("team admin set a Vantage telemetry token", out.getvalue())
        self.assertIn("Vantage accepted it", out.getvalue())

    def test_status_reports_a_revoked_team_token(self):
        self.record(401)
        out = io.StringIO()

        self.assertEqual(configure.status(self.env, out), 1)
        self.assertIn("rejected the token", out.getvalue())

    def test_setup_skips_sign_in_when_the_team_token_works(self):
        self.record(200)
        out = io.StringIO()

        self.assertEqual(configure.setup(self.env, out, open_browser=self.fail, wait=self.fail), 0)
        self.assertIn("no sign-in is needed", out.getvalue())

    def test_setup_signs_in_when_only_a_personal_send_was_recorded(self):
        self.record(200, source="file")
        opened = []

        with self.assertRaises(configure.SetupError):
            configure.setup(
                dict(self.env, VANTAGE_CURSOR_INGEST_ENDPOINT="http://127.0.0.1:9"),
                io.StringIO(),
                open_browser=opened.append,
                wait=lambda _url, _verifier: "vntg_ti_new",
            )

        self.assertEqual(len(opened), 1)


class StatusAndRemoveTest(ConfigureTestCase):
    def test_status_reports_rejected_token(self):
        with FakeServer(default=(401, {})) as server:
            configure.write_config({"token": "vntg_ti_revoked1234", "endpoint": server.url}, self.env)
            out = io.StringIO()

            code = configure.status(self.env, out)

        self.assertEqual(code, 1)
        self.assertIn("ending in 1234", out.getvalue())
        self.assertIn("rejected", out.getvalue())

    def test_status_without_setup(self):
        out = io.StringIO()

        self.assertEqual(configure.status(self.env, out), 1)
        self.assertIn("not set up", out.getvalue())

    def test_remove_deletes_saved_token_and_flags_managed_environment(self):
        configure.write_config({"token": "vntg_ti_saved"}, self.env)
        out = io.StringIO()

        configure.remove(dict(self.env, VANTAGE_CURSOR_INGEST_TOKEN="managed"), out)

        self.assertFalse(os.path.exists(self.env["VANTAGE_CURSOR_TELEMETRY_CONFIG"]))
        self.assertIn("still set in the environment", out.getvalue())

    def test_hook_and_setup_agree_on_config_location(self):
        env = {"XDG_CONFIG_HOME": self.tmp.name}

        self.assertEqual(configure.config_path(env), hook.config_path(env))
        self.assertEqual(configure.config_path(env), os.path.join(self.tmp.name, "vantage", "cursor-telemetry.json"))


if __name__ == "__main__":
    unittest.main()
