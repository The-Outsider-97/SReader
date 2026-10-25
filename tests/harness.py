"""Shared set-up for the tests: real settings in a temporary folder, the real engines, real fixtures."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests_py"))
import fixtures  # noqa: E402  (also puts the SLAI root and the app on sys.path)

from slai_reader.annotations.service import AnnotationService  # noqa: E402
from slai_reader.annotations.store import Store  # noqa: E402
from slai_reader.bridge import DocumasterLink, ReaderEngines, activate  # noqa: E402
from slai_reader.config import Settings  # noqa: E402
from slai_reader.documents.loader import open_document  # noqa: E402

ROOT = fixtures.ROOT


class Env:
    """One temporary data folder with settings, engines and (when Documaster is present) the Documaster link."""

    def __init__(self) -> None:
        self.docs: list = []
        self.tmp = tempfile.TemporaryDirectory(prefix="reader-test-")
        self.dir = Path(self.tmp.name)
        self.settings = Settings(slai_root=ROOT, data_dir=self.dir / "data", output_dir=self.dir / "out")
        self.settings.ensure_dirs()
        activate(ROOT)
        self.engines = ReaderEngines()
        self.link = DocumasterLink(ROOT, self.settings.data_dir, "none")
        self.store = Store(self.settings.db_path, self.settings.backups_dir)

    def open(self, path: Path, **kw):
        return open_document(Path(path), settings=self.settings, engines=self.engines, link=self.link, **kw)

    def service(self, path: Path, **kw) -> AnnotationService:
        doc = self.open(path, **kw)
        self.store.touch_document({"doc_id": doc.doc_id, "path": str(path), "title": doc.title, "format": doc.format, "layout": doc.layout,
                                   "size": Path(path).stat().st_size, "fingerprint": doc.fingerprint, "revision": doc.revision,
                                   "pages": getattr(doc, "pages", None)})
        self.docs.append(doc)
        return AnnotationService(doc, self.store)

    def make(self, name: str, maker, *a, **kw) -> Path:
        return maker(self.dir / name, *a, **kw)

    def close(self) -> None:
        for d in self.docs:
            d.close()
        self.store.close()
        self.tmp.cleanup()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.env = Env()
        self.addCleanup(self.env.close)
