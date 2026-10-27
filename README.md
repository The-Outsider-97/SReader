# SLAI Reader

Read and annotate documents on your own computer. Part of SLAI v2.4, in `applications/Reader`.

Reader opens **SLDC, PDF, EPUB, DOCX, ODT, RTF, Markdown, plain text and HTML**, remembers where you stopped, and lets you highlight,
underline, strike through, write notes, add sticky notes, draw, bookmark and tag. Your files are opened read-only and are never
changed. Everything stays on this computer. An optional assistant (summary, related passages) uses SLAI agents only if you allow it
for that document, and says plainly when it could not.

## Run it

```
python -m pip install -r requirements.txt
python -m slai_reader                    # opens your browser on http://127.0.0.1:8767
python -m slai_reader paper.pdf notes.sldc
python -m slai_reader --handoff reader-to-writer-....json
```

`dist/` (the web client) is included, so Node is only needed to change the client: `node tools/build-static.mjs`.

| Option | Meaning |
|---|---|
| `--port 8767` | Documaster uses 8766. Reader only listens on the loopback address; any other `--host` is refused. |
| `--agents auto\|none\|strict` | `none` never creates an agent. Default `auto`. A document's text still goes to an agent only after you allow it for that document and the Privacy agent agrees. |
| `--data-dir`, `--output-dir` | Library and annotations (default `%LOCALAPPDATA%\SLAI\Reader`), exports (default `Documents\SLAI Reader`). |
| `--writer-command "..."` | Program that starts SLAI Writer; `{handoff}` is replaced by the hand-off file. Also `SLAI_WRITER_CMD`. |
| `--allow-network-paths` | Allow files on network shares. Off by default. |
| `--diagnose-storage` | Checks, step by step, that settings can be saved in the data folder (create, write, flush, replace, replace over an existing file), reports redirected folders and the exact error number of any failing step, then exits. Add `--json` for machine-readable output. It only creates and removes its own probe files. |
| `--slai-root` | The SLAI checkout. Found automatically from the app's own location. |

Stopping the program with Ctrl+C or `SIGTERM` (Linux, tested) or Ctrl+Break (Windows, not tested) writes a final backup of any pending changes. A hard kill does not.

## If a setting will not save

Reader names what failed instead of guessing: the message and the `storage_*` code say whether the drive is full, the folder is read-only, another program holds the file, the path is unusable, or the folder is a junction or redirected location that Windows will not rename files within. The technical details (step, error number, `winerror`) are printed in Reader's console window; they never include your settings, documents or the access token.

Run `python -m slai_reader --diagnose-storage` (add `--data-dir ...` for a custom folder) to see which step fails on this computer. A Microsoft Store build of Python redirects `%LOCALAPPDATA%`; if the report points there, start Reader with `--data-dir` set to an ordinary folder. See [docs/USER_GUIDE.md](docs/USER_GUIDE.md#when-settings-will-not-save) for the codes.

## Where things are

```
slai_reader/            Python service: documents/ (formats), annotations/ (anchors, store, export), api, sessions, assist, interop, bridge
src/client, src/core    TypeScript client (strict, no innerHTML); core/ is DOM-free and unit-tested
dist/                   built client served by the service
tests/                  Python unit and HTTP tests          python -m unittest discover -s tests
tests_e2e/              Playwright tests in real Chromium   python -m unittest discover -s tests_e2e -p "test_*.py"
tests_ts/               TypeScript tests                    npm test   (needs `npm install`)
docs/                   USER_GUIDE.md, REPORTS.md (reports A-H and the checklist), benchmarks.json, screenshots/
```

## How it fits into SLAI

* **SLDC** is read and written only through `src/utils/sldc.py`. Reader defines no competing format.
* **Reader engines** (`src/agents/reader`: detection, parsing, integrity) identify what a file really is.
* **Documaster** is imported for DOCX, ODT, HTML, Markdown and RTF reading and for its agent gateway; none of its files are changed. If it is missing Reader still opens SLDC and PDF and says which formats it lost.
* **Hand-over** uses one JSON file, `slai-handoff/1`, in `<SLAI data>/handoff`. Reader writes and reads it. Documaster does not read it yet and SLAI Writer is not part of this checkout; REPORTS.md says what that means.

## Read before relying on it

[docs/REPORTS.md](docs/REPORTS.md) lists what was measured, on what machine, and what is not done. The short version: tested on Linux headless Chromium,
not yet on Windows 11; no OCR for scanned PDFs; the real SLAI agents were not run end to end; annotations live in Reader's own store (and can
be exported), not inside your files unless you export a PDF copy.
