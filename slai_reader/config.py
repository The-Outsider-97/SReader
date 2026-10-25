"""Runtime settings. Every value has a safe default; environment variables and command-line flags override it."""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

MIB = 1024 * 1024


def _default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "SLAI" / "Reader"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "slai" / "reader"


def _default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.exists() else Path.home()) / "SLAI Reader"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8767                               # Documaster uses 8766
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    slai_root: Path | None = None
    data_dir: Path = field(default_factory=_default_data_dir)
    output_dir: Path = field(default_factory=_default_output_dir)
    # Limits: every one is enforced before untrusted content is parsed.
    max_open_bytes: int = field(default_factory=lambda: _env_int("SLAI_READER_MAX_OPEN_MB", 512) * MIB)
    max_upload_bytes: int = field(default_factory=lambda: _env_int("SLAI_READER_MAX_UPLOAD_MB", 200) * MIB)
    max_pdf_pages: int = field(default_factory=lambda: _env_int("SLAI_READER_MAX_PDF_PAGES", 20000))
    max_text_chars: int = field(default_factory=lambda: _env_int("SLAI_READER_MAX_TEXT_CHARS", 20_000_000))
    max_sessions: int = 12
    session_ttl_s: int = 12 * 3600
    max_render_pixels: int = 60_000_000
    render_cache_mb: int = 96
    process_timeout_s: int = field(default_factory=lambda: _env_int("SLAI_READER_TIMEOUT_S", 120))
    # SLAI agents are optional. ``auto`` uses them when they load, ``none`` never creates one, ``strict`` turns an agent that
    # cannot be created into an error. Whatever the mode, a document's text is only passed to an agent after the person has
    # allowed it for that document (``agent_read_default`` changes that default) and the Privacy agent has agreed.
    agents: str = "auto"
    agent_read_default: bool = False
    agent_timeout_s: float = field(default_factory=lambda: float(_env_int("SLAI_READER_AGENT_TIMEOUT_S", 60)))
    allow_network_paths: bool = False
    writer_command: str | None = field(default_factory=lambda: os.environ.get("SLAI_WRITER_CMD") or None)
    open_browser: bool = True

    # ------------------------------------------------------------------ derived locations
    @property
    def db_path(self) -> Path:
        return self.data_dir / "reader.sqlite"

    @property
    def prefs_path(self) -> Path:
        return self.data_dir / "prefs.json"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def handoff_dir(self) -> Path:
        return self.data_dir.parent / "handoff"      # shared by SLAI applications: <SLAI data>/handoff

    @property
    def is_windows(self) -> bool:
        return sys.platform.startswith("win")

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.backups_dir, self.uploads_dir, self.handoff_dir):
            path.mkdir(parents=True, exist_ok=True)

    def public(self) -> dict:
        return {"output_dir": str(self.output_dir), "max_upload_mb": self.max_upload_bytes // MIB,
                "max_open_mb": self.max_open_bytes // MIB, "agents": self.agents,
                "agent_read_default": self.agent_read_default, "max_pdf_pages": self.max_pdf_pages,
                "writer_configured": bool(self.writer_command)}
