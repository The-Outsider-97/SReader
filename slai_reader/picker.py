"""A native "open file" dialog, shown by the machine that runs Reader (which is the person's own computer).

It runs in a separate short-lived process: GUI toolkits insist on the main thread of their own process, and a crash or a missing
toolkit must not take the server down. If no dialog can be shown the client falls back to its own file chooser (upload).
"""
from __future__ import annotations

import json
import subprocess
import sys

SCRIPT = r"""
import json, sys
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception as exc:
    print(json.dumps({"error": "no-dialog", "detail": str(exc)[:200]})); sys.exit(0)
try:
    root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
    types = [("Documents", "*.sldc *.pdf *.epub *.docx *.odt *.rtf *.txt *.md *.markdown *.html *.htm *.xhtml"), ("All files", "*.*")]
    path = filedialog.askopenfilename(title="Open a document in SLAI Reader", filetypes=types)
    print(json.dumps({"path": path or None}))
except Exception as exc:
    print(json.dumps({"error": "no-dialog", "detail": str(exc)[:200]}))
"""


def pick_file(timeout: float = 600.0) -> dict:
    try:
        proc = subprocess.run([sys.executable, "-I", "-c", SCRIPT], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": "no-dialog", "detail": str(exc)[:200]}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"error": "no-dialog", "detail": (proc.stderr or "")[-200:]}
