"""Regression coverage for opening authenticated, password-protected SLDC files."""
from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
