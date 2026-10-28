"""Safe preference commits, the shared atomic write, and the way storage failures are classified and reported.

Windows-specific errors (``winerror`` 17, 32, 5 ...) are SIMULATED here by raising ``OSError`` objects that carry them. These tests
prove how Reader reacts to such an error; they do not prove that a real Windows volume behaves that way. Real behaviour is checked
on a computer with ``python -m slai_reader --diagnose-storage`` (see ``test_diagnose_storage``-style tests below for the command itself).
"""
from __future__ import annotations

import errno
import json
import os
import sqlite3
import tempfile
import threading
import unittest

from pathlib import Path
from unittest.mock import patch

from apiharness import ApiBase
from slai_reader import files
from slai_reader.diagnose import diagnose
from slai_reader.errors import STORAGE_REASONS, ReaderError, StorageError
from slai_reader.files import atomic_write, classify_oserror, sanitize_path, storage_reason
from slai_reader.prefs import PrefsFile


def win_error(winerror: int, code: int, text: str = "simulated") -> OSError:
    exc = OSError(code, text)
    exc.winerror = winerror          # type: ignore[attr-defined]  # simulated Windows error
    return exc


EXDEV_WIN17 = lambda: win_error(17, errno.EXDEV, "The system cannot move the file to a different disk drive")   # noqa: E731


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="reader-prefs-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def leftovers(self, folder: Path | None = None) -> list[str]:
        return sorted(p.name for p in (folder or self.dir).iterdir() if p.name.endswith((".tmp", ".bak")))


class Classification(unittest.TestCase):
    def test_each_error_lands_in_its_own_class(self) -> None:
        cases = [
            (win_error(17, errno.EXDEV), "cross_volume"), (OSError(errno.EXDEV, "x"), "cross_volume"),
            (win_error(112, errno.ENOSPC), "disk_full"), (OSError(errno.ENOSPC, "x"), "disk_full"),
            (win_error(32, errno.EACCES), "locked"), (win_error(33, errno.EACCES), "locked"),
            (win_error(5, errno.EACCES), "permission_denied"), (OSError(errno.EROFS, "x"), "permission_denied"),
            (win_error(123, errno.EINVAL), "invalid_path"), (win_error(206, errno.ENOENT), "invalid_path"),
            (OSError(errno.ENAMETOOLONG, "x"), "invalid_path"), (win_error(50, errno.EINVAL), "unsupported"),
            (OSError(errno.EIO, "x"), "unexpected"),
        ]
        for exc, want in cases:
            with self.subTest(winerror=getattr(exc, "winerror", None), errno=exc.errno):
                self.assertEqual(storage_reason(exc, "replace"), want)

    def test_sharing_violation_is_not_confused_with_access_denied(self) -> None:
        # Both arrive as errno EACCES on Windows; only winerror tells them apart.
        self.assertEqual(storage_reason(win_error(32, errno.EACCES), "replace"), "locked")
        self.assertEqual(storage_reason(win_error(5, errno.EACCES), "replace"), "permission_denied")

    def test_every_reason_has_a_status_and_a_person_readable_hint(self) -> None:
        for reason, (status, hint) in STORAGE_REASONS.items():
            self.assertGreaterEqual(status, 400)
            self.assertTrue(hint.endswith("."), reason)

    def test_details_are_complete_and_free_of_the_home_folder(self) -> None:
        exc = win_error(17, errno.EXDEV)
        exc.filename = str(Path.home() / "AppData" / "x.tmp")
        exc.filename2 = str(Path.home() / "AppData" / "prefs.json")
        err = classify_oserror(exc, "replace", Path.home() / "AppData" / "prefs.json", label="your settings", unchanged=True)
        d = err.details
        self.assertEqual((err.code, d["step"], d["errno"], d["winerror"], d["exception"]), ("storage_cross_volume", "replace", errno.EXDEV, 17, "OSError"))
        self.assertEqual(d["strerror"], "simulated")
        for key in ("path", "filename", "filename2"):
            self.assertTrue(d[key].startswith("~"), (key, d[key]))
            self.assertNotIn(str(Path.home()), d[key])
        self.assertTrue(d["previous_kept"])
        self.assertIsInstance(err, OSError)                    # old `except OSError` guards still catch it
        self.assertIsInstance(err, ReaderError)

    def test_sanitize_path_leaves_other_paths_alone(self) -> None:
        self.assertEqual(sanitize_path("/var/data/x"), "/var/data/x")


class AtomicWrite(TempDirCase):
    def test_normal_write_replaces_existing_content_and_leaves_nothing_behind(self) -> None:
        target = self.dir / "prefs.json"
        atomic_write(target, b"one")
        atomic_write(target, b"two")
        self.assertEqual(target.read_bytes(), b"two")
        self.assertEqual(self.leftovers(), [])

    def test_unicode_and_space_paths(self) -> None:
        target = self.dir / "Réader données 设置" / "my prefs ☃.json"
        atomic_write(target, "ünï".encode("utf-8"))
        atomic_write(target, "ünï2".encode("utf-8"))
        self.assertEqual(target.read_bytes(), "ünï2".encode("utf-8"))
        self.assertEqual(self.leftovers(target.parent), [])

    def test_refused_rename_is_reported_with_its_real_cause_and_not_worked_around(self) -> None:
        target = self.dir / "prefs.json"
        atomic_write(target, b"original")
        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()) as replace:
            with self.assertRaises(StorageError) as caught:
                atomic_write(target, b"new data", label="your settings")
        err = caught.exception
        self.assertEqual((err.code, err.details["step"], err.details["winerror"], err.details["errno"]), ("storage_cross_volume", "replace", 17, errno.EXDEV))
        self.assertEqual(replace.call_count, 1, "a permanent error must not be retried")
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(self.leftovers(), [])
        self.assertTrue(err.details["temp_removed"])

    def test_no_copy_and_delete_without_opt_in(self) -> None:
        target = self.dir / "export.json"
        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()), patch("slai_reader.files._write_in_place") as inplace:
            with self.assertRaises(StorageError):
                atomic_write(target, b"x")
        inplace.assert_not_called()
        self.assertFalse(target.exists())

    def test_in_place_fallback_keeps_previous_content_until_the_new_content_is_verified(self) -> None:
        target = self.dir / "prefs.json"
        atomic_write(target, b'{"v": 1}')
        seen_backup: list[bytes] = []
        real_read = Path.read_bytes

        def spy(self_: Path) -> bytes:                                    # runs after the new content was written: the backup must exist then
            data = real_read(self_)
            if self_ == target and data == b'{"v": 2}':
                seen_backup.append(real_read(target.with_name("prefs.json.bak")))
            return data

        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()), patch.object(Path, "read_bytes", spy):
            atomic_write(target, b'{"v": 2}', inplace_fallback=True)
        self.assertEqual(seen_backup, [b'{"v": 1}'])
        self.assertEqual(target.read_bytes(), b'{"v": 2}')
        self.assertEqual(self.leftovers(), [], "temp and backup are removed after verification")

    def test_in_place_fallback_restores_previous_content_when_writing_fails(self) -> None:
        target = self.dir / "prefs.json"
        atomic_write(target, b'{"v": 1}')
        real_open = open

        class Failing:
            def __init__(self, handle): self.h = handle
            def __enter__(self): return self
            def __exit__(self, *a): self.h.close(); return False
            def write(self, data): self.h.write(data[:3]); raise OSError(errno.ENOSPC, "No space left on device")
            def flush(self): self.h.flush()
            def fileno(self): return self.h.fileno()

        fail_once = {"left": 1}

        def fake_open(file, mode="r", *a, **k):
            if Path(file) == target and mode == "wb" and fail_once["left"]:
                fail_once["left"] -= 1
                return Failing(real_open(file, mode))
            return real_open(file, mode, *a, **k)

        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()), patch.object(files, "open", fake_open, create=True):
            with self.assertRaises(StorageError) as caught:
                atomic_write(target, b'{"v": 2}', inplace_fallback=True)
        err = caught.exception
        self.assertEqual((err.code, err.details["step"]), ("storage_disk_full", "in_place_write"))
        self.assertEqual(err.details["replace_error"]["winerror"], 17)
        self.assertTrue(err.details["previous_kept"])
        self.assertEqual(target.read_bytes(), b'{"v": 1}')
        self.assertEqual([p for p in self.leftovers() if p.endswith(".tmp")], [])

    def test_momentary_lock_is_retried_a_bounded_number_of_times(self) -> None:
        target = self.dir / "prefs.json"
        calls = {"n": 0}
        real = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise win_error(32, errno.EACCES, "The process cannot access the file because it is being used by another process")
            return real(src, dst)

        sleeps: list[float] = []
        with patch("slai_reader.files.os.replace", flaky), patch.object(files, "_sleep", sleeps.append):
            atomic_write(target, b"ok")
        self.assertEqual((calls["n"], len(sleeps)), (3, 2))
        self.assertEqual(target.read_bytes(), b"ok")

    def test_permanent_lock_gives_up_after_the_bound_and_reports_locked(self) -> None:
        target = self.dir / "prefs.json"
        atomic_write(target, b"old")
        sleeps: list[float] = []
        with patch("slai_reader.files.os.replace", side_effect=win_error(32, errno.EACCES)) as replace, patch.object(files, "_sleep", sleeps.append):
            with self.assertRaises(StorageError) as caught:
                atomic_write(target, b"new")
        self.assertEqual(replace.call_count, len(files._RETRY_DELAYS) + 1)
        self.assertLessEqual(sum(sleeps), 1.0)
        self.assertEqual((caught.exception.code, caught.exception.details["retries"]), ("storage_locked", len(files._RETRY_DELAYS)))
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(self.leftovers(), [])

    def test_errors_that_are_not_momentary_are_not_retried(self) -> None:
        for exc in (OSError(errno.EACCES, "denied"), OSError(errno.EIO, "io"), OSError(errno.ENOSPC, "full")):
            with self.subTest(errno=exc.errno), patch("slai_reader.files.os.replace", side_effect=exc) as replace:
                with self.assertRaises(StorageError):
                    atomic_write(self.dir / "p.json", b"x")
                self.assertEqual(replace.call_count, 1)

    def test_each_step_is_named(self) -> None:
        target = self.dir / "sub" / "prefs.json"
        cases = {
            "mkdir": patch.object(Path, "mkdir", side_effect=PermissionError(errno.EACCES, "denied")),
            "create_temp": patch("slai_reader.files.tempfile.mkstemp", side_effect=OSError(errno.ENOSPC, "full")),
            "fsync": patch("slai_reader.files.os.fsync", side_effect=OSError(errno.EIO, "io")),
        }
        for step, ctx in cases.items():
            with self.subTest(step=step), ctx:
                with self.assertRaises(StorageError) as caught:
                    atomic_write(target, b"x")
                self.assertEqual(caught.exception.details["step"], step)
        self.assertEqual(self.leftovers(target.parent) if target.parent.exists() else [], [])

    def test_disk_full_while_writing_cleans_the_temp_file(self) -> None:
        target = self.dir / "prefs.json"
        atomic_write(target, b"old")
        real_fdopen = os.fdopen

        def full(fd, mode="r", *a, **k):
            handle = real_fdopen(fd, mode, *a, **k)
            handle.write = lambda data: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left on device"))   # type: ignore[method-assign]
            return handle

        with patch("slai_reader.files.os.fdopen", full):
            with self.assertRaises(StorageError) as caught:
                atomic_write(target, b"new")
        self.assertEqual((caught.exception.code, caught.exception.details["step"]), ("storage_disk_full", "write"))
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(self.leftovers(), [])

    def test_destination_that_is_a_directory_is_an_invalid_path_not_a_crash(self) -> None:
        (self.dir / "prefs.json").mkdir()
        with self.assertRaises(StorageError) as caught:
            atomic_write(self.dir / "prefs.json", b"x")
        self.assertIn(caught.exception.reason, ("permission_denied", "invalid_path", "unexpected"))
        self.assertEqual(self.leftovers(), [])

    def test_parent_that_is_a_file_is_an_invalid_path(self) -> None:
        (self.dir / "data").write_text("not a folder")
        with self.assertRaises(StorageError) as caught:
            atomic_write(self.dir / "data" / "prefs.json", b"x")
        self.assertEqual((caught.exception.reason, caught.exception.details["step"]), ("invalid_path", "mkdir"))

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs POSIX permissions and a non-root user")
    def test_read_only_folder_is_reported_as_permission_denied(self) -> None:
        folder = self.dir / "ro"
        folder.mkdir()
        target = folder / "prefs.json"
        atomic_write(target, b"keep")
        folder.chmod(0o555)
        self.addCleanup(folder.chmod, 0o755)
        with self.assertRaises(StorageError) as caught:
            atomic_write(target, b"new")
        self.assertEqual(caught.exception.reason, "permission_denied")
        self.assertEqual(target.read_bytes(), b"keep")


class PreferencePersistence(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.dir / "prefs.json"
        self.prefs = PrefsFile(self.path)
        self.prefs.update({"theme": "sepia", "fontSize": 24})

    def test_update_failure_preserves_memory_and_saved_values(self) -> None:
        previous = self.prefs.get()
        data = self.path.read_bytes()
        with patch("slai_reader.prefs.atomic_write", side_effect=OSError(errno.EACCES, "write denied")):
            with self.assertRaises(OSError):
                self.prefs.update({"theme": "dark", "fontSize": 36})
        self.assertEqual(self.prefs.get(), previous)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(PrefsFile(self.path).get(), previous)
        self.assertEqual(self.prefs.update({"theme": "light"})["theme"], "light")

    def test_reset_failure_preserves_memory_and_saved_values(self) -> None:
        previous = self.prefs.get()
        data = self.path.read_bytes()
        with patch("slai_reader.prefs.atomic_write", side_effect=OSError(errno.EACCES, "write denied")):
            with self.assertRaises(OSError):
                self.prefs.reset()
        self.assertEqual(self.prefs.get(), previous)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(PrefsFile(self.path).get(), previous)
        self.assertEqual(self.prefs.reset()["theme"], "system")

    def test_failed_atomic_replace_preserves_destination_and_cleans_temp(self) -> None:
        original = self.path.read_bytes()
        with patch("slai_reader.files.os.replace", side_effect=OSError(errno.EXDEV, "cross-device rename")):
            with self.assertRaises(StorageError):
                atomic_write(self.path, b"new data")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.dir.glob(".prefs.json.*.tmp")), [])

    def test_saves_keep_working_where_the_rename_is_refused(self) -> None:
        """The reported Windows failure: every ``os.replace`` fails with ERROR_NOT_SAME_DEVICE (simulated). Settings must still save."""
        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()):
            self.assertEqual(self.prefs.update({"theme": "dark"})["theme"], "dark")
            self.assertEqual(self.prefs.update({"fontSize": 30})["fontSize"], 30)       # a second save, destination now exists
            self.assertEqual(self.prefs.reset()["theme"], "system")
        reloaded = PrefsFile(self.path)
        self.assertEqual((reloaded.get()["theme"], reloaded.get()["fontSize"]), ("system", self.prefs.get()["fontSize"]))
        self.assertEqual(self.leftovers(), [])

    def test_failed_in_place_save_keeps_the_previous_settings_in_memory_and_on_disk(self) -> None:
        before_memory, before_disk = self.prefs.get(), self.path.read_bytes()
        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()), \
                patch("slai_reader.files._write_in_place", side_effect=OSError(errno.EACCES, "denied")):
            with self.assertRaises(StorageError) as caught:
                self.prefs.update({"theme": "dark"})
        self.assertEqual(caught.exception.code, "storage_permission_denied")
        self.assertEqual((self.prefs.get(), self.path.read_bytes()), (before_memory, before_disk))

    def test_interrupted_in_place_write_is_recovered_from_the_backup(self) -> None:
        good = self.prefs.get()
        self.path.write_bytes(b'{"schema": 1, "values": {"theme": "da')           # cut off mid-write
        self.path.with_name("prefs.json.bak").write_bytes(json.dumps({"schema": 1, "values": good}).encode())
        loaded = PrefsFile(self.path)
        self.assertEqual((loaded.get()["theme"], loaded.get()["fontSize"]), ("sepia", 24))
        self.assertIn("restored", loaded.recovered or "")
        self.assertTrue(list(self.dir.glob("prefs.json.corrupt-*")), "the damaged file is kept, not deleted")
        self.assertFalse(self.path.with_name("prefs.json.bak").exists())

    def test_missing_settings_file_with_a_backup_is_restored(self) -> None:
        good = self.prefs.get()
        self.path.unlink()
        self.path.with_name("prefs.json.bak").write_text(json.dumps({"schema": 1, "values": good}))
        loaded = PrefsFile(self.path)
        self.assertEqual(loaded.get()["theme"], "sepia")
        self.assertTrue(self.path.exists())

    def test_damaged_file_without_backup_still_falls_back_to_defaults_and_keeps_the_file(self) -> None:
        self.path.write_text("{broken")
        loaded = PrefsFile(self.path)
        self.assertEqual(loaded.get()["theme"], "system")
        self.assertTrue(list(self.dir.glob("prefs.json.corrupt-*")))

    def test_rapid_saves_from_many_threads_end_in_a_valid_file(self) -> None:
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for i in range(15):
                    self.prefs.update({"fontSize": 12 + (n * 15 + i) % 36, "theme": ("light", "dark", "sepia")[i % 3]})
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        stored = PrefsFile(self.path).get()
        self.assertEqual(stored, self.prefs.get(), "disk and memory agree after the last save")
        self.assertEqual(self.leftovers(), [])

    def test_rapid_saves_in_the_refused_rename_mode_also_end_valid(self) -> None:
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for i in range(10):
                    self.prefs.update({"fontSize": 12 + (n * 10 + i) % 36})
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()):
            threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertEqual(PrefsFile(self.path).get(), self.prefs.get())
        self.assertEqual(self.leftovers(), [])

    def test_existing_valid_file_with_unicode_folder(self) -> None:
        path = self.dir / "Données Reader ☃" / "prefs.json"
        prefs = PrefsFile(path)
        prefs.update({"theme": "dark"})
        self.assertEqual(PrefsFile(path).get()["theme"], "dark")


class PreferenceApiPersistence(ApiBase):
    def put(self, body):
        return self.api.call("PUT", "/api/prefs", body)

    def current(self):
        return self.api.data(200, self.api.get("/api/prefs"))["values"]

    def test_save_succeeds_where_the_rename_is_refused(self) -> None:
        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()):
            status, js = self.put({"theme": "sepia"})
        self.assertEqual(status, 200, js)
        self.assertEqual(self.current()["theme"], "sepia")

    def test_cross_volume_failure_has_its_own_code_details_and_leaves_settings_unchanged(self) -> None:
        original = self.current()
        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()), \
                patch("slai_reader.files._write_in_place", side_effect=EXDEV_WIN17()):
            status, js = self.put({"theme": "sepia"})
        err = js["error"]
        self.assertEqual((status, err["code"]), (500, "storage_cross_volume"))
        self.assertEqual((err["details"]["step"], err["details"]["winerror"], err["details"]["errno"]), ("in_place_write", 17, errno.EXDEV))
        self.assertEqual(err["details"]["replace_error"]["winerror"], 17)
        self.assertNotIn("atomic file replacement across volumes", err["message"])
        self.assertEqual(self.current(), original)

    def test_each_failure_class_is_reported_distinctly(self) -> None:
        cases = {
            "storage_permission_denied": (403, win_error(5, errno.EACCES)),
            "storage_locked": (423, win_error(32, errno.EACCES)),
            "storage_disk_full": (507, win_error(112, errno.ENOSPC)),
            "storage_invalid_path": (500, win_error(123, errno.EINVAL)),
            "storage_unsupported": (501, win_error(50, errno.EINVAL)),
            "storage_unexpected": (500, OSError(errno.EIO, "I/O error")),
        }
        original = self.current()
        for code, (want_status, exc) in cases.items():
            with self.subTest(code=code), patch("slai_reader.files.os.replace", side_effect=exc), \
                    patch("slai_reader.files._write_in_place", side_effect=exc), patch.object(files, "_sleep", lambda s: None):
                status, js = self.put({"theme": "dark"})
                self.assertEqual((status, js["error"]["code"]), (want_status, code))
                self.assertTrue(js["error"]["message"])
                self.assertEqual(self.current(), original)

    def test_error_response_contains_no_settings_values_or_tokens(self) -> None:
        with patch("slai_reader.files.os.replace", side_effect=win_error(5, errno.EACCES)), patch.object(files, "_sleep", lambda s: None):
            status, js = self.put({"theme": "sepia", "textColor": "#123456"})
        blob = json.dumps(js)
        self.assertNotIn("123456", blob)
        self.assertNotIn(self.api.settings.token, blob)

    def test_unclassified_os_errors_from_elsewhere_are_reported_by_their_real_error(self) -> None:
        with patch.object(self.api.app.prefs, "update", side_effect=win_error(1224, errno.EIO, "user-mapped section open")):
            status, js = self.put({"theme": "dark"})
        self.assertEqual(js["error"]["code"], "storage_unexpected")
        self.assertEqual(js["error"]["details"]["winerror"], 1224)

    def test_reset_failure_is_reported_and_keeps_settings(self) -> None:
        self.assertEqual(self.put({"theme": "dark"})[0], 200)
        with patch("slai_reader.files.os.replace", side_effect=OSError(errno.EIO, "io")):
            status, js = self.api.call("POST", "/api/prefs/reset", {})
        self.assertEqual((status, js["error"]["code"]), (500, "storage_unexpected"))
        self.assertEqual(self.current()["theme"], "dark")

    def test_document_preferences_failure_is_reported_and_independent_of_global_ones(self) -> None:
        from harness import fixtures
        d = self.api.open(self.make("p.sldc", fixtures.make_sldc))
        sid = d["sid"]
        self.assertEqual(self.put({"theme": "dark"})[0], 200)
        store = self.api.app.sessions.store
        with patch.object(store, "set_doc_prefs", side_effect=sqlite3.OperationalError("database is locked")):
            status, js = self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {"fontSize": 30}})
        self.assertEqual((status, js["error"]["code"]), (423, "storage_locked"))
        self.assertEqual(self.current()["theme"], "dark", "a failed document save must not touch the global settings")
        ok = self.api.data(200, self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {"fontSize": 30}}))
        self.assertEqual(ok["values"]["fontSize"], 30)
        self.assertEqual(self.current()["fontSize"], self.api.data(200, self.api.get("/api/prefs"))["defaults"]["fontSize"])


class ConfirmedSettingsRoute(ApiBase):
    def test_get_returns_confirmed_values_without_writing(self) -> None:
        from harness import fixtures
        sid = self.api.open(self.make("c.sldc", fixtures.make_sldc))["sid"]
        self.api.data(200, self.api.call("PUT", "/api/prefs", {"theme": "sepia"}))
        self.api.data(200, self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {"fontSize": 30}}))
        prefs_file = self.api.settings.prefs_path
        before = prefs_file.read_bytes()
        store = self.api.app.sessions.store
        with patch.object(store, "set_doc_prefs", side_effect=AssertionError("a read must not write")), \
                patch("slai_reader.files.os.replace", side_effect=AssertionError("a read must not write")):
            got = self.api.data(200, self.api.get(f"/api/doc/{sid}/prefs"))
        self.assertEqual(got["docPrefs"], {"fontSize": 30})
        self.assertEqual((got["values"]["theme"], got["effective"]["values"]["fontSize"], got["effective"]["theme"]), ("sepia", 30, "sepia"))
        self.assertEqual(prefs_file.read_bytes(), before)

    def test_global_change_response_does_not_hide_document_overrides_in_the_confirmed_state(self) -> None:
        from harness import fixtures
        sid = self.api.open(self.make("d.sldc", fixtures.make_sldc))["sid"]
        self.api.data(200, self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {"fontSize": 30}}))
        self.api.data(200, self.api.call("PUT", "/api/prefs", {"theme": "dark"}))
        got = self.api.data(200, self.api.get(f"/api/doc/{sid}/prefs"))
        self.assertEqual((got["effective"]["values"]["fontSize"], got["effective"]["values"]["theme"]), (30, "dark"))

    def test_unknown_session_is_not_found(self) -> None:
        self.assertEqual(self.api.get("/api/doc/nope/prefs")[0], 404)


class DiagnoseStorage(TempDirCase):
    def test_reports_every_step_ok_and_removes_its_probe_files(self) -> None:
        folder = self.dir / "Reader data"
        report = diagnose(folder)
        self.assertTrue(report["ok"], report)
        self.assertEqual([s["step"] for s in report["steps"]],
                         ["mkdir", "create_temp", "write", "fsync", "replace_new_destination", "replace_existing_destination",
                          "atomic_write_twice", "atomic_write_with_fallback", "read_back"])
        self.assertEqual(list(folder.iterdir()), [])

    def test_names_the_failing_step_and_the_error_number(self) -> None:
        with patch("slai_reader.files.os.replace", side_effect=EXDEV_WIN17()):
            report = diagnose(self.dir / "d")
        failed = [s for s in report["steps"] if not s["ok"]]
        self.assertFalse(report["ok"])
        self.assertEqual((failed[0]["step"], failed[0]["reason"], failed[0]["winerror"]), ("replace_new_destination", "cross_volume", 17))
        self.assertIn("--data-dir", report["hint"])
        self.assertEqual([p for p in (self.dir / "d").iterdir()], [])

    def test_never_touches_existing_settings(self) -> None:
        folder = self.dir / "d"
        folder.mkdir()
        (folder / "prefs.json").write_text('{"schema": 1, "values": {"theme": "dark"}}')
        before = (folder / "prefs.json").read_bytes()
        diagnose(folder)
        self.assertEqual((folder / "prefs.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
