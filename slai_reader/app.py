"""The application object: everything the HTTP layer needs, created once."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__
from .annotations.store import Store
from .assist import Assistant
from .bridge import DocumasterLink, ReaderEngines, find_slai_root, activate
from .config import Settings
from .prefs import PrefsFile
from .sessions import SessionManager

log = logging.getLogger("slai_reader")


class ReaderApp:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_dirs()
        if settings.slai_root is None:
            settings.slai_root = find_slai_root(None)
        activate(settings.slai_root)
        self.started = time.time()
        self.engines = ReaderEngines()
        self.link = DocumasterLink(settings.slai_root, settings.data_dir, settings.agents, settings.agent_timeout_s)
        self.link.load()
        self.store = Store(settings.db_path, settings.backups_dir)
        self.prefs = PrefsFile(settings.prefs_path)
        self.sessions = SessionManager(settings, self.engines, self.link, self.store, self.prefs)
        self.assistant = Assistant(settings, self.link)
        self.pending: list[tuple[str, str]] = []
        self.startup_notes: list[str] = []
        if self.store.recovered:
            r = self.store.recovered
            self.startup_notes.append(f"The annotation database was damaged and was replaced. It was kept aside ({', '.join(r['quarantined']) or 'nothing to move'}); "
                                      f"{r['annotations']} annotation(s) in {r['documents']} document(s) were restored from the latest backups.")
        if self.prefs.recovered:
            self.startup_notes.append(self.prefs.recovered)

    def status(self) -> dict[str, Any]:
        from .formats import FORMATS
        return {"app": APP_NAME, "version": __version__, "uptimeS": round(time.time() - self.started),
                "platform": sys.platform, "python": sys.version.split()[0], "settings": self.settings.public(),
                "sldc": self._sldc_info(), "engines": self.engines.status(), "documaster": self.link.status(),
                "store": {"path": str(self.settings.db_path), "integrity": self.store.integrity(), "recovered": self.store.recovered},
                "formats": [{"id": f.id, "label": f.label, "extensions": list(f.exts), "layout": f.layout} for f in FORMATS.values()],
                "startupNotes": self.startup_notes}

    @staticmethod
    def _sldc_info() -> dict[str, Any]:
        from .bridge import sldc
        s = sldc()
        return {"module": s.__name__, "formatVersion": getattr(s, "FORMAT_VERSION", None), "legacyVersion": getattr(s, "LEGACY_VERSION", None)}

    def close(self) -> None:
        self.sessions.close_all()
        self.link.close()
        self.store.close()
