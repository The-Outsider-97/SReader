# SLAI Reader - reports and definition of done

Version 2.4.0. Everything below is something that was run or read in this checkout; where it was not run, it says so.
Numbers are from the machine in report G (2 vCPU Linux VM, headless Chromium 153), **not from Windows 11**.

| | |
|---|---|
| Python tests (`tests/`) | 112 tests, all pass |
| Browser tests (`tests_e2e/`, real Chromium, real mouse and keyboard) | 45 tests, all pass |
| TypeScript unit tests (`tests_ts/`) | 16 tests, all pass; `tsc --strict` clean |
| Not run | Windows 11, NVDA/Narrator, a real SLAI agent end to end, a real SLAI Writer, a scanned-PDF OCR path (there is none) |

---

## A. Repository assessment

What the SLAI checkout offered, and what Reader does with it. Verified on the user's folder (read-only) and in the working copy.

| Part | State found | What Reader does |
|---|---|---|
| `src/utils/sldc.py` | The authoritative SLDC implementation (read, write, validate, anchors, comments, signatures, encryption). **Untracked in git** in the user's checkout (`?? src/utils/sldc.py`). | Uses it for every SLDC operation. Defines no format of its own. Commit it with Reader. |
| `src/agents/reader` | `DetectionEngine`, `ParserEngine`, `IntegrityEngine` and others (job, salvage, conversion, compression). | Uses detection and integrity to say what a file is and whether it is damaged. The other engines are not needed. |
| `applications/Documaster` | A document service with DOCX/ODT/HTML/Markdown readers and an `AgentGateway`. Also **untracked** (`?? applications/Documaster/`). | Imported through one named surface (`DocumasterLink`, covered by `tests/test_interop.py::Contract`). None of its files is modified. |
| Documaster RTF route | Goes through DOCX; **fails with python-docx 1.2** on the alignment values LibreOffice writes. | Reader converts RTF to ODT with LibreOffice and uses Documaster's ODT reader. Documaster is not changed; the finding should be fixed there. |
| SLAI Writer | **Not in this checkout.** | Hand-over writes the `slai-handoff/1` file and starts Writer only if `SLAI_WRITER_CMD` / `--writer-command` names a program. |
| Hand-over consumer in Documaster | None. Documaster does not read hand-over files. | Reader starts Documaster and shows the path; the position and selection are in the file for when Documaster reads it. Said in the interface. |
| Port | Documaster uses 8766. | Reader uses 8767. |
| Platform | Windows 11 primary. | Paths, file locks (the damaged-database recovery closes the connection before moving the file, because Windows cannot move an open file), `%LOCALAPPDATA%`, UNC paths (refused unless allowed) and the system file dialog are written for Windows; only Linux was available for testing. |

Decision: a local web application. A Python standard-library HTTP service on loopback serves a strict-TypeScript client built to static files
(no framework, no `innerHTML`, CSP without `unsafe-inline`/`unsafe-eval`), because SLAI's other applications work this way and it needs no installer.

## B. Implementation report

### What exists

| Area | Where | Notes |
|---|---|---|
| Documents | `slai_reader/documents/` | `pdf.py` (pypdfium2 behind one lock; page images, text, per-character boxes, background text index), `flow.py` (SLDC and everything converted to SLDC in memory), `epub.py`, `loader.py` (identify by bytes, size and page limits before parsing, passwords), `appearance.py` (reading modes for PDF pixels and for text). |
| Annotations | `slai_reader/annotations/` | `anchors.py` (quote + prefix + suffix selectors; status `ok` / `moved` / `detached`), `store.py` (SQLite WAL; revision-checked updates; soft delete; rolling JSON snapshots; damaged-database quarantine and restore), `service.py` (create/update/delete/undo/redo, resolve, carry-over), `history.py`, `export.py`, `model.py` (validation: the client cannot choose a quote). |
| API | `api.py`, `router.py`, `server.py`, `sessions.py` | Loopback only; token to HttpOnly SameSite=Strict cookie; Host check; `X-SLAI-Reader` header and Origin check on mutations; limits enforced before parsing. |
| Assistant | `assist.py` | See report D. |
| Hand-over | `interop.py` | `slai-handoff/1`, report A. |
| Client | `src/client`, `src/core` | Library, PDF view (virtualised pages, text layer, marks layer, ink), flow view (virtualised blocks), side panel (contents, pages, notes, search, assistant), dialogs, settings, keyboard model, keyboard-only text selection (`keyselect.ts`). |

### Defects found by testing and fixed during this work

These were found by driving the real program, not by reading code:

* Page-count and zoom empty on open (a `build`/`goTo` deadlock); the selection toolbar closing right after the mouse was released;
  infinite recursion pressing Escape with a selected drawing; strike-through drawn at the top of the word; Home/End doing nothing in paged layouts;
  Ctrl+Enter not saving from the tags field; 640 px wide window overflowing sideways; a 16 px tall progress slider; no way to select text with the keyboard.
* **Scale:** listing the annotations of a 400-page PDF took 10.8 s and one page's marks 11 s, because every annotation measured every character of every
  page. Fixed by a cheap plain-text path (`plain_page`) and by resolving only the annotations stored on the requested page: now 99 ms and 0.23 s.
* **Backups:** the first change was not backed up until the tenth. Now the first change, every 10 changes or 15 s, and on close and on `SIGTERM`.
* **Windows:** the damaged-database recovery tried to move a file that was still open.
* **Plain text on a computer with an old `colorama`:** run on the user's own Python 3.10 environment, `.txt` and `.md` would not open because the SLAI Reader parser engine fails to start there
  (`module 'colorama' has no attribute 'just_fix_windows_console'`). Reader now reads plain text and Markdown directly into SLDC blocks when that happens and shows why on the document.
  Other formats still say what is missing. (The cause is in the SLAI environment: `colorama >= 0.4.6` fixes the engine itself.)
* **A flaky test of mine:** `test_detached_and_hidden_annotations_are_not_exported` picked the annotation to hide by list position, which is not fixed between page-level marks; it failed about 1 run in 12. It now picks by kind (15 repeats pass).
* **Right-to-left:** an Arabic paragraph in a document whose language says "ltr" was laid out left to right. Each paragraph now takes its direction from its own text.
* **Rotated pages:** the invisible text layer treated vertical (page rotated 90/270) and backwards (180) words as horizontal, giving boxes twice a line's thickness that overlapped the next line. The server now says which way each word runs and the client follows it.

### Deliberately not done

OCR; editing documents; accounts or sync; opening files from the internet; a shape tool (the stored model has a `shape` kind, the interface has no tool for it); touch drawing; DRM removal; running scripts or loading anything external from a document.

## C. Format matrix

"Verified" means a test in this repository opens a real file of that format and checks the behaviour named.

| Format | Shown as | Parsed by | Select + mark text | Draw / sticky / bookmark | Search | Export a marked copy | Verified by | Limits |
|---|---|---|---|---|---|---|---|---|
| SLDC | reflow | `src/utils/sldc.py` | yes, anchors survive edits | yes | yes | SLDC copy with review comments (keeps signatures and encryption) | e2e E, G; `test_export` | Document span colours ignored. |
| PDF | fixed pages | pypdfium2 + pypdf | yes (needs a text layer; rotated pages handled) | yes | yes (background index) | PDF with standard annotations; PDF with marks drawn in | e2e A-D, F, G, I; `test_annotations`, `test_export`, `test_pdf_scale` | Scans: no text, **no OCR**; encrypted PDFs need the password and stay encrypted; a PDF that forbids annotation is not modified. |
| EPUB | reflow | Reader EPUB reader + Documaster HTML reader | yes | yes | yes | notes only | e2e E | XHTML chapters only; fixed-layout EPUB and DRM refused. |
| DOCX | reflow | Documaster (python-docx) | yes | yes | yes | notes only | e2e E | Original page layout not reproduced; colours of spans not applied. |
| ODT | reflow | Documaster | yes | yes | yes | notes only | e2e E | as DOCX |
| RTF | reflow | LibreOffice -> ODT -> Documaster | yes | yes | yes | notes only | e2e E (LibreOffice present) | Needs LibreOffice; without it Reader says so and opens nothing. |
| Markdown | reflow | SLDC Markdown blocks | yes | yes | yes | notes only | e2e E | |
| Plain text | reflow | SLDC text blocks | yes | yes | yes | notes only | e2e E | |
| HTML | reflow | Documaster HTML reader | yes | yes | yes | notes only | e2e E | Scripts, styles and external resources are never run or fetched. |

Notes-only formats: the person's notes leave as Markdown, JSON or an SLDC document (the file itself is never altered).
Refused with a message (tested): damaged files, wrong extension for the content, legacy `.doc`, DRM EPUB, over-size files, files that are not documents.

## D. Agent matrix

Reader's assistant uses SLAI agents only through Documaster's `AgentGateway`. **No real agent was run in these tests** (none could be loaded here);
`tests/test_interop.py::AssistantRules` drives Reader's own gate and checks with stand-in agents installed by the gateway's `inject` hook, and the
tests are named and documented as plumbing tests. What follows says what Reader does with each role, and what is proven.

| Role | Used for | Reader's rule (enforced in `assist.py`, tested with stand-ins) | Without it |
|---|---|---|---|
| Privacy | Gate on every use | Nothing is sent unless the person ticked "Let SLAI agents read this document"; "allow" is the only answer that lets text through (`modify` and `deny` keep it local) | Local fallback; the reason is shown |
| Safety | Review before the language agent | A block verdict stops the request | n/a (privacy gate alone then decides) |
| Language | Summary; re-ranking of passages | A summary is checked against the document by Documaster's `abstract_summary` (>= 60 % of its content words must occur in it) and otherwise discarded with the reason shown; an unrecognised ranking answer is discarded | Key sentences picked by word frequency (quoted, labelled "not AI-written"); passages ranked by matching words |
| Quality | Document check | Receives counts per page, never text | Local counts only |
| Reasoning, Knowledge, Planning, Handler, Observability | Not used | Answers here are quotations of the document; no inference, no telemetry | - |

`--agents none` switches all of it off (tested). Whether the real agents give good summaries is **unverified**.

## E. Annotation compatibility

| Kind | Stored as | PDF copy with standard annotations | PDF copy with marks drawn in | SLDC copy with comments | Notes (Markdown / JSON) |
|---|---|---|---|---|---|
| Highlight, underline, strike-through | text anchor (quote, prefix, suffix, segments) + colour | `/Highlight`, `/Underline`, `/StrikeOut` with quad points (tested on rotated pages) | painted into the page | only if it carries a note or title (as a review comment on the range) | yes |
| Note on text | as above + text + tags | translucent `/Highlight` that carries the note text | painted | review comment | yes |
| Sticky note | page or block position + text | `/Text` annotation (Note icon) | painted | review comment when it has text | yes |
| Ink (pen, pencil, highlighter pen) | normalised strokes | `/Ink` | painted | **not included** (the dialog says so) | listed as "drawing, shown in the document"; geometry in JSON |
| Bookmark | position + title | entries under a "Reader bookmarks" outline item | - | not included | yes |
| Shape | in the model; **no tool in the interface** | - | - | - | - |

* The opened file is never changed. Exports are new files, created exclusively (an existing file is never overwritten; tested).
* JSON export re-imports into another copy of Reader (round trip tested), duplicates are skipped and unplaceable ones listed.
* Detached and hidden annotations are not written into marked copies (tested).
* Anything an annotation sits on is re-checked on open; a changed file shows moved / detached marks and offers "Carry them over" when a PDF's identity changed.
* Other PDF programs showing these annotations: written to the standard; read back by pypdf and drawn by pdfium in the tests. **Acrobat, Edge and Preview were not tried.**

## F. UI and accessibility report

Tested in real Chromium with the browser's accessibility tree and my own WCAG-based rules (`axe-core` could not be installed offline), not with a screen reader.

| Check | Result | Test |
|---|---|---|
| Every control has an accessible name and role (library and document) | pass | `test_every_control_has_a_name_library_and_document` |
| Landmarks, headings, document semantics | pass | `test_landmarks_and_document_semantics` |
| Text contrast >= 4.5:1 (UI) in light, dark, sepia, high contrast | pass | `test_contrast_in_every_reading_mode`, `test_contrast_of_flow_text_in_every_mode` |
| Keyboard only: open, read, annotate | pass | `test_keyboard_only_open_read_annotate` |
| Keyboard-only text selection and marking | pass | `test_selecting_and_marking_text_without_a_mouse` |
| Dialog traps focus and returns it; popover Escape returns focus | pass | `test_dialog_traps_focus_and_returns_it`, `test_popover_escape_returns_focus` |
| Reflow at 200 % zoom (320 px) with no sideways scroll; targets >= 24 px | pass | `test_reflow_at_200_percent_and_target_sizes` |
| Reduced motion; forced-colours | pass | `test_reduced_motion_and_forced_colors` |
| System dark mode followed when chosen | pass | `test_system_dark_mode_is_followed_when_asked` |

Not verified: NVDA / JAWS / Narrator speech, Windows high-contrast themes on Windows, touch input (drawing is pen/mouse only), 400 % zoom beyond the 200 % reflow test.
Screenshots of each workflow are in `docs/screenshots/`.

## G. Test report

### Suites

| Suite | Count | What it proves |
|---|---|---|
| `tests/test_store.py` | 15 | Schema and integrity; newer schema refused untouched; corrupt/truncated database quarantined and restored from snapshot; corrupt snapshot skipped; snapshots rolling and atomic; first change backed up at once, again after 10 changes / 15 s; pending changes backed up on close; revision conflicts; concurrent writers lose nothing; two processes share one store; `kill -9` during writes leaves a consistent store |
| `tests/test_annotations.py` | 26 | Anchors, validation (client cannot choose the quote), duplicates, undo/redo, conflicts, edit-follow (moved/detached/re-anchor), PDF rectangles including rotated pages, scanned PDFs, word directions |
| `tests/test_export.py` | 14 | Every export, round trip, signatures and encryption kept, never overwrite, rotated pages |
| `tests/test_api.py` | 27 | The whole stack over HTTP; opening every format; security (no cookie, strict cookie, DNS rebinding, marker header + Origin, headers, path traversal, oversize) |
| `tests/test_pdf_scale.py` | 4 | `plain_page` equals the measured text (rotation, astral characters); lists and marks do not measure pages; marks read one page; background index |
| `tests/test_interop.py` | 26 | Documaster surface; hand-over file in and out, malformed files refused; assistant gate (stand-ins) |
| `tests_e2e` A-I | 45 | Real browser: read, annotate, ink, navigate, text formats, failures, export, accessibility, edge cases |
| `tests_ts` | 16 | Offsets, geometry, keys, text ranges |

### Failure cases (all in a real browser)

Password-protected PDF (and cancelling the prompt); scanned PDF (honest about text, drawing still works); damaged, missing and wrong files; file replaced while open
(re-check and carry-over); file deleted then found elsewhere; damaged annotation database (set aside, restored from backup, user told); service stopped while reading.

### Performance (`tests_e2e/bench.py`, `docs/benchmarks.json`)

Machine: Intel Xeon 2.1 GHz, 2 cores, 8 GB, Linux 6.18, Python 3.13, Chromium 153 headless. Fixtures: a 400-page PDF, a 17,000-paragraph SLDC, 2,000 highlights.

| Measure | Result |
|---|---|
| Open 400-page PDF (server) | 184 ms |
| Browser: open the big PDF to the first page | 1.0 s (was 21 s before the scale fix) |
| Page image, p50 / p95 | 23 ms / 31 ms |
| Page text (with character boxes), p50 | 76 ms |
| Search, first / second call (400 pages) | 12 ms / 62 ms |
| Open 1,000 / 3,000-page PDF and finish the text index | 0.2 s open, 1.2 s / 4.1 s index; search 7-8 ms |
| Create 2,000 highlights in bulk | 4.5 ms each |
| Annotation list with 2,000 | 99 ms (was 10.8 s) |
| Single annotation create, p50 / p95 | 76 / 109 ms (includes the fsync) |
| One page's marks, p50 / p95 / max | 232 / 316 / 1,665 ms (the max is the first touch of a page) |
| Scroll the whole 400-page PDF | 1,146 steps; frame p50 16.7 ms, p95 22.7 ms, max 63 ms; 1 frame over 50 ms; at most 5 page images in the DOM; JS heap 13 MB |
| Open the 17,000-paragraph SLDC | 1.35 s; scroll frame p50 16.7 ms, p95 29.7 ms; 11 frames over 50 ms on the first sweep; at most 1,400 blocks in the DOM |
| Server memory | 37 MB idle -> 190 MB after the large documents |

These are one machine, one run each, headless. They are measurements, not guarantees; run `python tests_e2e/bench.py --pages 400 --marks 2000` on the target machine.

## H. User documentation

[USER_GUIDE.md](USER_GUIDE.md) (what it does, every shortcut, what is kept where, limits) and [../README.md](../README.md) (running it, options, layout).

---

## Definition of done

Marked **done** only with evidence named; otherwise **partly** or **not done**, with the reason.

| # | Requirement | Status | Evidence / reason |
|---|---|---|---|
| 1 | Opens SLDC, PDF, EPUB, DOCX, ODT, RTF, TXT, MD, HTML | done | `test_api::test_open_every_format`, e2e E, report C. RTF needs LibreOffice. |
| 2 | Appearance: light/dark/sepia/high contrast, zoom 25-400 %, layouts, font size/colour/opacity | done | e2e A, E, H; contrast tests. PDF text cannot be restyled (part of the page image). |
| 3 | Annotations: highlight, underline, strike, notes, sticky, ink, bookmarks, tags | done | e2e B, C; `test_annotations`. Shape tool: **not done**. |
| 4 | Stable anchors; moved/detached recovery | done | `test_annotations` (edit-follow, re-anchor), e2e E (live edit), F (file replaced, carry-over) |
| 5 | Atomic autosave, backups, conflict detection, recovery | done | `test_store` (including `kill -9`, two processes), e2e F. Hard kill + corrupt database can lose up to 15 s of changes. |
| 6 | Navigation, search, library | done | e2e D; `test_pdf_scale` |
| 7 | Optional AI assistant, privacy-gated, never fabricated | partly | Gate and fallbacks proven with stand-in agents (`AssistantRules`). **Real agents not run end to end.** |
| 8 | Documaster and Writer interop | partly | Documaster surface and `slai-handoff/1` file proven (`test_interop`). Documaster has no consumer; Writer is not in the checkout. |
| 9 | Security | done | `test_api::Security`, CSP, loopback-only, untrusted-input limits. No external review. |
| 10 | Measured performance | done | Report G, on Linux; **Windows 11 not measured.** |
| 11 | Accessibility | partly | Report F. No screen-reader run. |
| 12 | Tests | done | Report G. |
| 13 | Phased delivery | done | Reader works with Documaster absent for SLDC and PDF (`DocumasterLink.available`); assistant, hand-over and converted formats degrade with a stated reason. |
| 14 | No dummy reader, no simulated annotations, no hardcoded results, no decorative controls, no fabricated AI | done, with a caveat | Annotations are read back from SQLite by the tests (`rows()`), never from the page; assistant output is quoted from the document or discarded (`AssistantRules`); every workflow was driven with the real mouse and keyboard; a control that did nothing (a redundant "Go to" button) was removed. It is **not** an exhaustive audit that every control changes something: the accessibility test only checks each control has a name. |
| 15 | No silent document corruption | done | Opened files are read-only; exports never overwrite (`test_output_never_overwrites`, `test_native_export...leaves_original_alone`); encrypted and no-annotate PDFs are not altered. |
| 16 | Production-ready claim | **not claimed** | Not run on Windows 11, no screen-reader test, no real-agent run, no OCR, shape tool missing. |

## Known limits (for the next person)

* No OCR; scanned pages cannot be searched or highlighted as text.
* Library-wide search covers titles, notes, highlighted text and bookmarks only.
* Pagination of reflowable documents in one-page layouts is approximate; the toolbar shows "page 1 of 1" for them.
* The eraser removes whole strokes; a drawing on a reflowable document belongs to the paragraph where it started; touch does not draw.
* Single annotation create is about 76 ms (a durable write); first marks of a PDF page can take 1.6 s on a 400-page file.
* A `ResourceWarning` about unclosed SQLite connections appears in the Python tests only (test teardown), not in the program.
* Documaster's RTF route needs a fix for python-docx 1.2; Reader does not depend on it.
