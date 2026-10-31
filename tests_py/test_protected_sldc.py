"""Regression coverage for opening authenticated, password-protected SLDC files."""
from __future__ import annotations

import tempfile
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from slai_reader.config import Settings
from slai_reader.documents.loader import _open_sldc
from slai_reader.errors import DependencyMissing, PasswordRequired, WrongPassword


class ProtectedSLDCTests(unittest.TestCase):
    def test_password_flow_and_missing_crypto_error_are_typed(self):
        try:
            from src.utils import sldc
            import cryptography  # noqa: F401 - the actual encryption dependency
        except ImportError:
            self.skipTest("SLAI and cryptography are required for encrypted SLDC integration tests")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locked.sldc"
            package = sldc.new_package("Secret", [sldc.make_block("paragraph", "Private content", block_id="p1")])
            sldc.write_package(path, package, overwrite=True,
                               recipients=[sldc.password_recipient("correct horse")])
            settings = Settings()
            size = path.stat().st_size
            with self.assertRaises(PasswordRequired) as missing:
                _open_sldc(path, "hash", size, None, settings)
            self.assertEqual(missing.exception.status, 401)
            with self.assertRaises(WrongPassword) as incorrect:
                _open_sldc(path, "hash", size, "wrong", settings)
            self.assertEqual(incorrect.exception.code, "wrong_password")
            document = _open_sldc(path, "hash", size, "correct horse", settings)
            self.assertTrue(document.encrypted)
            self.assertEqual(document.doc["blocks"][0]["text"], "Private content")

            with patch.object(sldc, "read_package", side_effect=sldc.SLDCDependencyError("cryptography absent")):
                with self.assertRaises(DependencyMissing) as missing_crypto:
                    _open_sldc(path, "hash", size, "correct horse", settings)
            self.assertIn("cryptography", str(missing_crypto.exception))
            with patch.object(sldc, "read_package", side_effect=sldc.SLDCDependencyError("scrypt is unavailable in this Python build")):
                with self.assertRaises(DependencyMissing) as missing_scrypt:
                    _open_sldc(path, "hash", size, "correct horse", settings)
            self.assertIn("scrypt is unavailable", str(missing_scrypt.exception))

    def test_http_open_and_native_picker_use_password_retry_contract(self):
        from src.utils import sldc
        from slai_reader.app import ReaderApp
        from slai_reader.server import ReaderHTTPServer

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "protected.sldc"
            pkg = sldc.new_package("Protected", [sldc.make_block("paragraph", "Read after unlocking", block_id="p1")])
            sldc.write_package(path, pkg, overwrite=True,
                               recipients=[sldc.password_recipient("correct horse")])
            settings = Settings(host="127.0.0.1", port=0, token="test-secret", agents="none",
                                slai_root=Path(sldc.__file__).resolve().parents[2],
                                data_dir=root / "data", output_dir=root / "output")
            app = ReaderApp(settings)
            server = ReaderHTTPServer(app, Path(__file__).resolve().parents[1] / "dist")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.addCleanup(app.close)

            def request(endpoint, body):
                data = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(f"http://127.0.0.1:{server.port}{endpoint}", data=data,
                    headers={"Content-Type": "application/json", "Cookie": "slai_reader_token=test-secret",
                             "X-SLAI-Reader": "1"}, method="POST")
                try:
                    response = urllib.request.urlopen(req, timeout=20)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    self.assertIn("application/json", response.headers.get("Content-Type", ""))
                    return response.status, json.loads(response.read())

            status, response = request("/api/open", {"path": str(path)})
            self.assertEqual((status, response["error"]["code"]), (401, "password_required"))
            status, response = request("/api/open", {"path": str(path), "password": "incorrect"})
            self.assertEqual((status, response["error"]["code"]), (401, "wrong_password"))
            status, response = request("/api/open", {"path": str(path), "password": "correct horse"})
            self.assertEqual(status, 200, response)
            self.assertTrue(response["ok"])
            self.assertIn("sid", response["data"])

            with patch("slai_reader.api.pick_file", return_value={"path": str(path)}):
                status, response = request("/api/pick", {})
            self.assertEqual(status, 200)
            self.assertEqual(response["data"]["picked"], str(path))
            self.assertTrue(response["data"]["passwordRequired"])
            self.assertNotIn("sid", response["data"])


if __name__ == "__main__":
    unittest.main()
