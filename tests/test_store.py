"""The annotation store under failure: killed mid-write, corrupted, truncated, newer schema, concurrent writers, snapshots."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import unittest

from pathlib import Path
from harness import Base, fixtures

from slai_reader.annotations import model
from slai_reader.annotations.store import Store
from slai_reader.errors import Conflict, StoreCorrupt

APP = Path(__file__).resolve().parents[1]


def ann(doc_id, n, kind="highlight"):
    stamp = model.now()
    return {"id": f"a{n:020d}", "doc_id": doc_id, "kind": kind, "anchor": {"kind": "block", "block": "b1"}, "style": {"color": "#facc15"},
            "body": {}, "created": stamp, "modified": stamp, "fingerprint": "f"}


class StoreTests(Base):
    def new_store(self, name="s"):
        d = self.env.dir / name
        s = Store(d / "reader.db", d / "backups")
        self.addCleanup(s.close)
        s.touch_document({"doc_id": "sldc:one", "title": "One", "format": "sldc", "layout": "flow", "path": "x", "size": 1, "fingerprint": "f"})
        return s, d

    def test_schema_version_and_integrity(self):
        s, _ = self.new_store()
        self.assertEqual(s.conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(s.integrity(), "ok")
        self.assertEqual(s.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_newer_schema_is_refused_and_left_untouched(self):
        s, d = self.new_store()
        s.conn.execute("PRAGMA user_version=99")
        s.close()
        before = (d / "reader.db").read_bytes()
        with self.assertRaises(StoreCorrupt) as cm:
            Store(d / "reader.db", d / "backups")
        self.assertIn("newer", str(cm.exception))
        self.assertEqual(cm.exception.details["found"], 99)
        self.assertEqual(sorted(p.name for p in d.iterdir() if "corrupt" in p.name), [])
        self.assertEqual(len((d / "reader.db").read_bytes()) > 0, True)

    def test_corrupt_database_is_quarantined_and_restored_from_snapshot(self):
        s, d = self.new_store()
        for i in range(5):
            s.insert(ann("sldc:one", i))
        s.set_position("sldc:one", {"block": "b1", "offset": 3}, 0.25)
        self.assertIsNotNone(s.snapshot("sldc:one"))
        s.close()
        (d / "reader.db").write_bytes(b"this is not a sqlite database" * 200)
        for suffix in ("-wal", "-shm"):
            if (d / ("reader.db" + suffix)).exists():
                (d / ("reader.db" + suffix)).unlink()
        s2 = Store(d / "reader.db", d / "backups")
        self.addCleanup(s2.close)
        self.assertIsNotNone(s2.recovered)
        self.assertEqual(s2.recovered["annotations"], 5)
        self.assertEqual(len(s2.list("sldc:one")), 5)
        self.assertEqual(s2.get_document("sldc:one")["position"]["offset"], 3)
        quarantined = [p for p in d.iterdir() if ".corrupt-" in p.name]
        self.assertTrue(quarantined, "the damaged file must be kept, not deleted")
        self.assertIn(b"this is not a sqlite", quarantined[0].read_bytes())

    def test_truncated_database_recovers(self):
        s, d = self.new_store()
        for i in range(30):
            s.insert(ann("sldc:one", i))
        s.snapshot("sldc:one")
        s.close()
        db = d / "reader.db"
        data = db.read_bytes()
        db.write_bytes(data[: len(data) // 3])
        for suffix in ("-wal", "-shm"):
            if (d / ("reader.db" + suffix)).exists():
                (d / ("reader.db" + suffix)).unlink()
        s2 = Store(db, d / "backups")
        self.addCleanup(s2.close)
        self.assertEqual(len(s2.list("sldc:one")), 30)

    def test_corrupt_snapshot_is_skipped_not_fatal(self):
        s, d = self.new_store()
        s.insert(ann("sldc:one", 1))
        snap = s.snapshot("sldc:one")
        s.close()
        snap.write_text("{ not json", encoding="utf-8")
        (d / "reader.db").write_bytes(b"x" * 5000)
        s2 = Store(d / "reader.db", d / "backups")
        self.addCleanup(s2.close)
        self.assertEqual(s2.recovered["annotations"], 0)
        self.assertEqual(s2.list("sldc:one"), [])

    def test_snapshots_are_rolling_and_atomic(self):
        s, d = self.new_store()
        s.insert(ann("sldc:one", 0))
        for _ in range(12):
            s.snapshot("sldc:one")
            time.sleep(0.002)
        files = list((s._snap_dir("sldc:one")).glob("*.json"))
        self.assertEqual(len(files), 8)
        self.assertEqual(list(s._snap_dir("sldc:one").glob("*.tmp*")), [])
        for f in files:
            self.assertEqual(json.loads(f.read_text())["annotations"][0]["id"], "a" + "0" * 20)

    def snaps(self, s, doc="sldc:one"):
        return sorted(s._snap_dir(doc).glob("*.json"))

    def test_first_change_is_backed_up_at_once_then_every_ten_changes(self):
        s, _ = self.new_store()
        s.insert(ann("sldc:one", 0))
        self.assertEqual(len(self.snaps(s)), 1, "the very first annotation must not wait for a backup")
        for i in range(1, 10):
            s.insert(ann("sldc:one", i))
        self.assertEqual(len(self.snaps(s)), 1, "nine more changes within the window: still the same snapshot")
        s.insert(ann("sldc:one", 10))                                      # the tenth change since the last snapshot
        self.assertEqual(len(self.snaps(s)), 2)

    def test_snapshot_after_the_time_window_even_with_few_changes(self):
        from slai_reader.annotations import store as store_module
        s, _ = self.new_store()
        s.insert(ann("sldc:one", 0))
        s._last_snapshot["sldc:one"] -= store_module.SNAPSHOT_SECONDS + 1  # pretend the last backup is old
        time.sleep(0.002)
        s.insert(ann("sldc:one", 1))
        self.assertEqual(len(self.snaps(s)), 2)

    def test_pending_changes_are_backed_up_on_close_and_survive_a_damaged_database(self):
        s, d = self.new_store()
        for i in range(4):
            s.insert(ann("sldc:one", i))                                   # only the first is snapshotted automatically
        self.assertEqual(json.loads(self.snaps(s)[-1].read_text())["annotations"].__len__(), 1)
        s.close()                                                          # a normal shutdown backs up what is pending
        (d / "reader.db").write_bytes(b"not a database" * 300)
        for suffix in ("-wal", "-shm"):
            if (d / ("reader.db" + suffix)).exists():
                (d / ("reader.db" + suffix)).unlink()
        s2 = Store(d / "reader.db", d / "backups")
        self.addCleanup(s2.close)
        self.assertEqual(len(s2.list("sldc:one")), 4)

    def test_conflict_on_stale_rev_and_soft_delete(self):
        s, _ = self.new_store()
        a = s.insert(ann("sldc:one", 1))
        s.update(a["id"], a, a["rev"])
        with self.assertRaises(Conflict):
            s.update(a["id"], a, a["rev"])
        s.delete(a["id"])
        self.assertEqual(s.list("sldc:one"), [])
        self.assertEqual(s.get(a["id"], include_deleted=True)["id"], a["id"])
        self.assertEqual(s.purge_deleted("sldc:one"), 1)

    def test_concurrent_writers_lose_nothing(self):
        s, _ = self.new_store()
        errors = []

        def work(k):
            try:
                for i in range(40):
                    s.insert(ann("sldc:one", k * 1000 + i))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=work, args=(k,)) for k in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(s.list("sldc:one")), 240)

    def test_two_processes_share_one_store(self):
        s, d = self.new_store()
        code = textwrap.dedent(f"""
            import sys; sys.path.insert(0, {str(APP)!r})
            from pathlib import Path
            from slai_reader.annotations.store import Store
            from slai_reader.annotations import model
            st = Store(Path({str(d / 'reader.db')!r}), Path({str(d / 'backups')!r}))
            for i in range(60):
                st.insert({{"id": f"p{{i:020d}}", "doc_id": "sldc:one", "kind": "highlight", "anchor": {{}}, "style": {{}}, "body": {{}},
                           "created": model.now(), "modified": model.now(), "fingerprint": "f"}})
            st.close()
        """)
        proc = subprocess.Popen([sys.executable, "-I", "-c", code])
        for i in range(60):
            s.insert(ann("sldc:one", 5000 + i))
        self.assertEqual(proc.wait(timeout=60), 0)
        self.assertEqual(len(s.list("sldc:one")), 120)

    def test_kill_nine_during_writes_leaves_a_consistent_store(self):
        """A writer process is forcibly terminated while saving; the store must retain only whole annotations."""
        _, d = self.new_store("kill")
        code = textwrap.dedent(f"""
            import sys, time; sys.path.insert(0, {str(APP)!r})
            from pathlib import Path
            from slai_reader.annotations.store import Store
            from slai_reader.annotations import model
            st = Store(Path({str(d / 'reader.db')!r}), Path({str(d / 'backups')!r}))
            print("ready", flush=True)
            i = 0
            while True:
                st.insert({{"id": f"k{{sys.argv[1]}}x{{i:018d}}", "doc_id": "sldc:one", "kind": "note", "anchor": {{"kind": "block", "block": "b"}}, "style": {{}},
                           "body": {{"text": "x" * 5000}}, "created": model.now(), "modified": model.now(), "fingerprint": "f"}})
                i += 1
                print(i, flush=True)
        """)
        survivors = []
        acked = 0
        for round_ in range(5):
            proc = subprocess.Popen([sys.executable, "-I", "-c", code, str(round_)], stdout=subprocess.PIPE, text=True)
            try:
                assert proc.stdout is not None
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                last = 0
                deadline = time.time() + 0.15 + 0.07 * round_
                while time.time() < deadline:
                    line = proc.stdout.readline().strip()
                    if line.isdigit():
                        last = int(line)
            finally:
                # Popen.kill() is SIGKILL on POSIX and TerminateProcess on Windows.
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
                assert proc.stdout is not None
                proc.stdout.close()
            reopened = Store(d / "reader.db", d / "backups")
            try:
                self.assertEqual(reopened.integrity(), "ok")
                self.assertIsNone(reopened.recovered, "a killed writer must not make the store look corrupt")
                rows = reopened.list("sldc:one")
                for r in rows:
                    self.assertEqual(len(r["body"]["text"]), 5000)       # whole records only
                ids = {r["id"] for r in rows}
                acked += last
                self.assertGreaterEqual(len(ids), acked)                 # every insert acknowledged to the caller survived
                survivors.append(len(ids))
            finally:
                reopened.close()
        self.assertGreater(survivors[-1], 0)

    def test_search_notes(self):
        s, _ = self.new_store()
        a = ann("sldc:one", 1, "note"); a["body"] = {"text": "Remember the Mitochondria", "tags": ["bio"]}
        b = ann("sldc:one", 2); b["anchor"] = {"kind": "text", "segments": [{"quote": "mitochondrial DNA"}]}
        c = ann("sldc:one", 3, "ink")
        for x in (a, b, c):
            s.insert(x)
        self.assertEqual(len(s.search_notes("mitochondri")), 2)
        self.assertEqual(len(s.search_notes("BIO")), 1)
        self.assertEqual(s.search_notes(""), [])

    def test_library_position_and_forget(self):
        s, _ = self.new_store()
        s.insert(ann("sldc:one", 1))
        s.set_position("sldc:one", {"block": "b9"}, 0.5)
        lib = s.library()
        self.assertEqual(lib[0]["doc_id"], "sldc:one")
        self.assertEqual(lib[0]["progress"], 0.5)
        s.forget_document("sldc:one")
        self.assertEqual(s.library(), [])
        self.assertEqual(len(s.list("sldc:one")), 1)         # hiding from Recent keeps the notes
        s.forget_document("sldc:one", delete_annotations=True)
        self.assertEqual(s.list("sldc:one"), [])


if __name__ == "__main__":
    unittest.main()
