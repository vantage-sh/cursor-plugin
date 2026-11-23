import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relative_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeServer:
    """Loopback HTTP server that records requests and answers from a queue of (status, body)."""

    def __init__(self, responses=None, default=(200, {})):
        self.requests = []
        self.responses = list(responses or [])
        self.default = default
        server = self

        class Handler(BaseHTTPRequestHandler):
            def respond(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                server.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "body": json.loads(raw or b"null")}
                )
                status, body = server.responses.pop(0) if server.responses else server.default
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = respond
            do_POST = respond

            def log_message(self, *_args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.httpd.shutdown()
        self.httpd.server_close()
