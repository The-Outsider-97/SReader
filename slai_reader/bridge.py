"""Connection to the SLAI checkout and to Documaster.

Reader consumes SLAI through its public modules only:

* ``src.utils.sldc``                    the authoritative SLDC implementation (read, validate, anchors, digests)
* ``src.agents.reader`` engines         ``DetectionEngine`` (what a file really is), ``ParserEngine`` (text extraction),
                                        ``IntegrityEngine`` (is a file well-formed)
* ``src.agents.agent_factory``          real SLAI agents, only through Documaster's gateway (below)

Documaster is consumed through one small, named surface (see :class:`DocumasterLink`) loaded under an alias package so
its ``slai`` package never clashes with anything of ours. Nothing of Documaster's is copied. If it is missing, Reader
still reads SLDC and PDF and says which formats and features it lost.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from .errors import DependencyMissing

_ROOT: Path | None = None
ALIAS = "documaster_slai"


# ---------------------------------------------------------------------------------------------- SLAI root
def looks_like_slai_root(path: Path) -> bool:
    return (path / "src" / "utils" / "sldc.py").is_file()


def find_slai_root(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("SLAI_ROOT"):
        candidates.append(Path(os.environ["SLAI_ROOT"]))
    candidates.extend(Path(__file__).resolve().parents)
    for candidate in candidates:
        try:
            if looks_like_slai_root(candidate):
                return candidate.resolve()
        except OSError:
            continue
    return None


def activate(root: Path | None) -> Path | None:
    """Make ``import src...`` work. The SLAI root goes last on ``sys.path`` so our own packages win."""
    global _ROOT
    if root is None:
        return None
    text = str(root)
    if text not in sys.path:
        sys.path.append(text)
    _ROOT = root
    return root


def slai_root() -> Path | None:
    return _ROOT


def sldc() -> ModuleType:
    """The authoritative SLDC implementation. Reader defines no competing format."""
    try:
        return importlib.import_module("src.utils.sldc")
    except ImportError as exc:
        raise DependencyMissing("The SLDC module (src/utils/sldc.py) was not found. Set SLAI_ROOT or pass --slai-root "
                                "to the SLAI checkout.", details={"import_error": str(exc)}) from exc


def have_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------------------------------------- Reader engines
_ENGINES = {
    "detector": ("src.agents.reader.detection_engine", "DetectionEngine"),
    "parser": ("src.agents.reader.parser_engine", "ParserEngine"),
    "integrity": ("src.agents.reader.integrity_engine", "IntegrityEngine"),
}


class ReaderEngines:
    """Lazy, thread-safe holder of the real SLAI Reader engines. A missing engine is reported, never faked."""

    def __init__(self) -> None:
        self._engines: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def engine(self, name: str) -> Any:
        with self._lock:
            if name in self._engines:
                return self._engines[name]
            if name in self._errors:
                raise DependencyMissing(f"Reader {name} engine is unavailable: {self._errors[name]}")
            module_name, class_name = _ENGINES[name]
            try:
                instance = getattr(importlib.import_module(module_name), class_name)()
            except Exception as exc:  # noqa: BLE001 - any import/constructor problem means "not available"
                self._errors[name] = f"{type(exc).__name__}: {str(exc)[:300]}"
                raise DependencyMissing(f"Reader {name} engine is unavailable: {self._errors[name]}") from exc
            self._engines[name] = instance
            return instance

    def available(self, name: str) -> bool:
        try:
            self.engine(name)
            return True
        except DependencyMissing:
            return False

    def status(self) -> dict[str, Any]:
        return {name: {"available": (ok := self.available(name)), "error": None if ok else self._errors.get(name)}
                for name in _ENGINES}

    def detect(self, path: str | Path) -> dict[str, Any]:
        """Reader's detection payload (``format_id``, ``family``, ``encrypted``, ``damaged`` ...), or ``{}`` if unavailable."""
        try:
            result = self.engine("detector").detect(str(path))
        except DependencyMissing:
            return {}
        except Exception as exc:  # noqa: BLE001
            return {"damaged": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        payload = result.get("payload") if isinstance(result, dict) else None
        return dict(payload or {})

    def parse(self, path: str | Path) -> dict[str, Any]:
        return self.engine("parser").parse(str(path))


# ---------------------------------------------------------------------------------------------- Documaster
class DocumasterLink:
    """The part of Documaster that Reader consumes, loaded under the alias package ``documaster_slai``.

    The surface is deliberately small and covered by contract tests (``tests_py/test_contract.py``):

    * ``docmodel.readers.read_document``   any supported file -> SLDC package (DOCX, ODT, HTML, MD, TXT, RTF via LibreOffice ...)
    * ``docmodel.read_html.read_html_bytes``   HTML -> blocks (used for EPUB chapters)
    * ``bridge.agents.AgentGateway`` and ``analysis.agent_calls``   the gateway to the real SLAI agents and the Privacy gate
    * ``analysis.pii``                     the personal-data detector the Privacy gate falls back on

    Documaster's own Python files are not modified. When Documaster is not installed ``available`` is False and ``reason`` says why.
    """

    def __init__(self, slai_root: Path | None, data_dir: Path, agents_mode: str = "auto", agent_timeout_s: float = 60.0) -> None:
        self.slai_root = slai_root
        self.data_dir = data_dir
        self.agents_mode = agents_mode
        self.agent_timeout_s = agent_timeout_s
        self.available = False
        self.reason: str | None = None
        self.version: str | None = None
        self.path: Path | None = None
        self.services: Any = None
        self._lock = threading.Lock()
        self._loaded = False

    def locate(self) -> Path | None:
        if self.slai_root is None:
            return None
        pkg = self.slai_root / "applications" / "Documaster" / "slai"
        return pkg if (pkg / "__init__.py").is_file() else None

    def load(self) -> "DocumasterLink":
        with self._lock:
            if self._loaded:
                return self
            self._loaded = True
            pkg = self.locate()
            if pkg is None:
                self.reason = "Documaster was not found under applications/Documaster of the SLAI checkout"
                return self
            self.path = pkg
            try:
                if ALIAS not in sys.modules:
                    spec = importlib.util.spec_from_file_location(ALIAS, pkg / "__init__.py", submodule_search_locations=[str(pkg)])
                    assert spec and spec.loader
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[ALIAS] = module
                    spec.loader.exec_module(module)
                top = sys.modules[ALIAS]
                self.version = getattr(top, "__version__", None)
                bridge = importlib.import_module(f"{ALIAS}.bridge")
                bridge.activate(self.slai_root)
                config = importlib.import_module(f"{ALIAS}.config")
                services = importlib.import_module(f"{ALIAS}.services")
                settings = config.Settings(agents=self.agents_mode, data_dir=self.data_dir / "documaster",
                                           agent_timeout_s=self.agent_timeout_s, agent_read_default=False)
                settings.slai_root = self.slai_root
                settings.data_dir.mkdir(parents=True, exist_ok=True)
                self.services = services.Services.create(settings)
                self.available = True
            except Exception as exc:  # noqa: BLE001
                sys.modules.pop(ALIAS, None)
                for name in [n for n in sys.modules if n.startswith(ALIAS + ".")]:
                    sys.modules.pop(name, None)
                self.reason = f"Documaster could not be loaded: {type(exc).__name__}: {str(exc)[:300]}"
            return self

    def module(self, dotted: str) -> ModuleType:
        if not self.available:
            raise DependencyMissing(self.reason or "Documaster is not available")
        return importlib.import_module(f"{ALIAS}.{dotted}")

    def read_document(self, path: Path, fmt: str, *, password: str | None = None, name: str | None = None) -> Any:
        """Documaster's reader: returns its ``IRResult`` (``.package``, ``.backend``, ``.warnings``, ``.dropped``, ``.features``)."""
        readers = self.module("docmodel.readers")
        return readers.read_document(path, fmt, self.services, password=password, name=name)

    def status(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "version": self.version,
                "path": str(self.path) if self.path else None}

    def close(self) -> None:
        if self.services is not None:
            try:
                self.services.agents.close()
            except Exception:  # noqa: BLE001
                pass
