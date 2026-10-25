"""A real Reader server on an ephemeral port, and a small HTTP client that behaves like the browser (cookie + marker header)."""
from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path

from harness import ROOT, fixtures  # noqa: F401  (sets up sys.path)

from slai_reader.app import ReaderApp
from slai_reader.config import Settings
from slai_reader.server import ReaderHTTPServer

import tempfile

APP_DIR = Path(__file__).resolve().parents[1]


class Api:
    def __init__(self, base_dir: Path, agents: str = "none", **settings_kw) -> None:
        self.settings = Settings(port=0, slai_root=ROOT, data_dir=base_dir / "data", output_dir=base_dir / "out", agents=agents, token="test-token-123", **settings_kw)
        self.app = ReaderApp(self.settings)
        self.server = ReaderHTTPServer(self.app, APP_DIR / "dist")
        self.port = self.server.port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.cookie = f"slai_reader_token={self.settings.token}"

    def request(self, method: str, path: str, body=None, *, headers=None, raw: bytes | None = None, cookie=True, marker=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = self.cookie
        if marker and method not in ("GET", "HEAD"):
            h["X-SLAI-Reader"] = "1"
        if host:
            h["Host"] = host
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        out = (resp.status, dict(resp.getheaders()), payload)
        conn.close()
        return out

    def call(self, method, path, body=None, **kw):
        status, headers, payload = self.request(method, path, body, **kw)
        try:
            return status, json.loads(payload)
        except ValueError:
            return status, payload

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, {} if body is None else body, **kw)

    def data(self, status_ok, resp):
        status, js = resp
        assert status == status_ok, (status, js)
        return js["data"] if isinstance(js, dict) and "data" in js else js

    def open(self, path, password=None):
        body = {"path": str(path)}
        if password:
            body["password"] = password
        return self.data(200, self.post("/api/open", body))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()


class ApiBase(unittest.TestCase):
    agents = "none"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="reader-api-")
        self.dir = Path(self.tmp.name)
        self.api = Api(self.dir, agents=self.agents)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.api.close)

    def make(self, name, maker, *a, **kw):
        return maker(self.dir / name, *a, **kw)
