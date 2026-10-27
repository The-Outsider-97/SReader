"""``python -m slai_reader --diagnose-storage``: find out, on this computer, which step of a save fails and why.

It only creates and removes its own uniquely named probe files inside the data folder. It never reads, changes or deletes
Reader's settings, library or backups, and its report contains no file contents (home folder is shown as ``~``).
"""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .files import (_replace_with_retry, atomic_write, os_error_info, sanitize_path, storage_reason)

_REPARSE = 0x400


def _link_facts(path: Path) -> dict[str, Any]:
    facts: dict[str, Any] = {"path": sanitize_path(path)}
    try:
        facts["exists"] = path.exists()
        facts["symlink"] = path.is_symlink()
        isjunction = getattr(os.path, "isjunction", None)
        if isjunction:
            facts["junction"] = bool(isjunction(path))
        info = os.lstat(path)
        attrs = getattr(info, "st_file_attributes", None)
        if attrs is not None:
            facts["reparse_point"] = bool(attrs & _REPARSE)
        tag = getattr(info, "st_reparse_tag", None)
        if tag:
            facts["reparse_tag"] = hex(tag)
        facts["device"] = info.st_dev
    except OSError as exc:
        facts["error"] = os_error_info(exc)
    return facts


def _volume(path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(260)
        k32 = ctypes.windll.kernel32                                     # type: ignore[attr-defined]
        if not k32.GetVolumePathNameW(str(path), buf, 260):
            return {"error": "GetVolumePathNameW failed"}
        root = buf.value
        kinds = {0: "unknown", 1: "no root", 2: "removable", 3: "fixed", 4: "network", 5: "cd-rom", 6: "ram disk"}
        name, fs = ctypes.create_unicode_buffer(260), ctypes.create_unicode_buffer(260)
        serial, maxlen, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        k32.GetVolumeInformationW(root, name, 260, ctypes.byref(serial), ctypes.byref(maxlen), ctypes.byref(flags), fs, 260)
        return {"volume_root": root, "drive_type": kinds.get(k32.GetDriveTypeW(root), "?"), "file_system": fs.value, "serial": hex(serial.value)}
    except Exception as exc:  # noqa: BLE001 - diagnostics must not fail because of a missing API
        return {"error": f"{type(exc).__name__}: {exc}"}


def _step(report: list[dict[str, Any]], name: str, fn: Callable[[], Any]) -> bool:
    try:
        fn()
    except OSError as exc:
        report.append({"step": name, "ok": False, "reason": storage_reason(exc, name), **os_error_info(exc)})
        return False
    except Exception as exc:  # noqa: BLE001
        report.append({"step": name, "ok": False, "reason": "unexpected", "exception": type(exc).__name__, "message": str(exc)[:200]})
        return False
    report.append({"step": name, "ok": True})
    return True


def diagnose(data_dir: Path) -> dict[str, Any]:
    data_dir = Path(data_dir)
    exe = sys.executable
    store_python = any(token in exe for token in ("WindowsApps", "PythonSoftwareFoundation")) or "WindowsApps" in sys.prefix
    resolved = Path(os.path.realpath(data_dir))
    chain = []
    for parent in [data_dir, *data_dir.parents]:
        chain.append(_link_facts(parent))
        if len(chain) > 12:
            break
    out: dict[str, Any] = {
        "python": {"version": platform.python_version(), "executable": sanitize_path(exe), "windows_store_python": store_python},
        "platform": platform.platform(),
        "data_dir": sanitize_path(data_dir),
        "data_dir_resolved": sanitize_path(resolved),
        "path_is_redirected": os.path.normcase(str(data_dir.resolve(strict=False))) != os.path.normcase(os.path.abspath(data_dir)),
        "volume": _volume(data_dir),
        "path_chain": chain,
        "steps": [],
    }
    steps: list[dict[str, Any]] = out["steps"]
    if not _step(steps, "mkdir", lambda: data_dir.mkdir(parents=True, exist_ok=True)):
        return _verdict(out)
    pid = os.getpid()
    dest = data_dir / f".reader-diagnose-{pid}.json"
    created: list[Path] = [dest, data_dir / f".reader-diagnose-{pid}.json.bak"]
    state: dict[str, Any] = {}

    def make_tmp() -> None:
        fd, name = tempfile.mkstemp(prefix=f".reader-diagnose-{pid}.", suffix=".tmp", dir=str(data_dir))
        state["tmp"] = name
        created.append(Path(name))
        state["fh"] = os.fdopen(fd, "wb")

    def write() -> None:
        state["fh"].write(b'{"probe": 1}')
        state["fh"].flush()

    def fsync() -> None:
        os.fsync(state["fh"].fileno())
        state["fh"].close()

    try:
        ok = _step(steps, "create_temp", make_tmp) and _step(steps, "write", write) and _step(steps, "fsync", fsync)
        if ok:
            _step(steps, "replace_new_destination", lambda: _replace_with_retry(state["tmp"], dest))
            def again() -> None:
                fd, name = tempfile.mkstemp(prefix=f".reader-diagnose-{pid}.", suffix=".tmp", dir=str(data_dir))
                created.append(Path(name))
                with os.fdopen(fd, "wb") as handle:
                    handle.write(b'{"probe": 2}')
                _replace_with_retry(name, dest)
            _step(steps, "replace_existing_destination", again)
        _step(steps, "atomic_write_twice", lambda: (atomic_write(dest, b'{"probe": 3}'), atomic_write(dest, b'{"probe": 4}')))
        _step(steps, "atomic_write_with_fallback", lambda: atomic_write(dest, b'{"probe": 5}', inplace_fallback=True))
        _step(steps, "read_back", lambda: _expect(dest.read_bytes(), b'{"probe": 5}'))
    finally:
        if "fh" in state:
            try:
                state["fh"].close()
            except OSError:
                pass
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
    return _verdict(out)


def _expect(got: bytes, want: bytes) -> None:
    if got != want:
        raise OSError("read-back differs")


def _verdict(out: dict[str, Any]) -> dict[str, Any]:
    failed = [s for s in out["steps"] if not s["ok"]]
    out["ok"] = not failed
    if not failed:
        out["summary"] = "Every step of a save works in this folder."
    else:
        first = failed[0]
        out["summary"] = (f"The first failing step is '{first['step']}' ({first.get('reason')}, "
                          f"winerror={first.get('winerror')}, errno={first.get('errno')}).")
        if any(s.get("reason") == "cross_volume" for s in failed):
            out["hint"] = ("Windows reports this folder as a different volume from its own temporary file. Look at 'path_chain' for a junction, "
                           "symlink or reparse point, and 'python.windows_store_python': a Microsoft Store Python redirects AppData. "
                           "Start Reader with --data-dir pointing at an ordinary folder (for example C:\\SLAIReaderData).")
    return out


def format_report(report: dict[str, Any]) -> str:
    lines = ["SLAI Reader storage check", f"  python      : {report['python']['version']} ({report['python']['executable']})"
             + ("  [Microsoft Store Python]" if report["python"]["windows_store_python"] else ""),
             f"  data folder : {report['data_dir']}", f"  resolves to : {report['data_dir_resolved']}"]
    if report.get("volume"):
        lines.append(f"  volume      : {json.dumps(report['volume'])}")
    for link in report["path_chain"]:
        flags = [k for k in ("symlink", "junction", "reparse_point") if link.get(k)]
        if flags:
            lines.append(f"  redirected  : {link['path']}  ({', '.join(flags)} {link.get('reparse_tag', '')})")
    for step in report["steps"]:
        if step["ok"]:
            lines.append(f"  [ ok ] {step['step']}")
        else:
            extra = {k: v for k, v in step.items() if k not in ("step", "ok")}
            lines.append(f"  [FAIL] {step['step']}: {json.dumps(extra, default=str)}")
    lines.append(f"  result      : {report['summary']}")
    if report.get("hint"):
        lines.append(f"  hint        : {report['hint']}")
    return "\n".join(lines)
