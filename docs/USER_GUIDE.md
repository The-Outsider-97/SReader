# SLAI Reader - user guide

Reader is for reading and marking up documents. It never changes the file you open. Your marks are kept by Reader next to its library
(`%LOCALAPPDATA%\SLAI\Reader` on Windows) and come back whenever you open the same document.

## Opening a document

* **Browse...** opens the system file dialog. **Choose a file** reads through the browser (Reader keeps a private copy; the card says so).
  You can also drop a file on the start page, type a full path and press Enter, or start Reader with the file name:
  `python -m slai_reader paper.pdf`. **Ctrl+O** opens another.
* The start page lists recent documents with how far you read and how many annotations they have. **Continue** returns to the exact place.
  **Forget** removes a document from the list; it asks separately whether to delete its annotations.
* Formats: SLDC, PDF, EPUB, DOCX, ODT, RTF, Markdown, plain text, HTML. Reader looks at the content, not only the extension, and
  tells you if they disagree.
* A password-protected PDF or encrypted SLDC asks for the password. Reader never removes protection, and does not keep the password.
* Not opened, with the reason shown: damaged files, DRM-protected EPUB, fixed-layout EPUB, legacy `.doc`, files over the size limit.
  A scanned PDF opens and can be read and drawn on, but has no text to select or search - Reader does not run OCR.

## Reading

| | |
|---|---|
| Zoom | 25-400 %. **Ctrl+=**, **Ctrl+-**, **Ctrl+0** (100 %), **Ctrl+1** fit width, **Ctrl+2** fit page. |
| Layout | Continuous scroll, one page at a time, two-page spread (with or without the cover on its own). Reflowable documents also have a reading-width and spacing control. |
| Reading mode | Light, dark, sepia, high contrast, or follow the system. Modes are semantic: PDFs are recoloured for dark and sepia without altering the file; text documents change text and page colours. "As printed" keeps a PDF's own colours. |
| Text | Typeface, size, alignment, line spacing, line length, text colour and opacity for reflowable documents (a PDF's text is part of its pages and is not restyled). |
| Focus | **F9** hides everything but the page; **Escape** returns. **F11** full screen. |
| Navigate | **Ctrl+G** go to a page or a percentage, **PageUp/PageDown**, **Ctrl+Home/End**, the page strip, contents (outline) and bookmarks in the side panel (**Ctrl+B** shows or hides it). In one-page and spread layouts also arrows and Space. |
| Search | **Ctrl+F** searches the open document, with the matches listed and marked. The start page searches titles, your notes, highlighted text and bookmarks across the library. |
| Settings | The Appearance button. "Apply these changes to this document only" keeps a document's own look; **Reset this document** removes it. |

Reader remembers the place, zoom and layout of every document and writes them when you stop, switch document or close.

## Marking up

Select text with the mouse, or with the keyboard: **Shift+Arrow** starts and extends a selection (Ctrl for words, Up/Down for lines).
A small toolbar appears: highlight (choose a colour), underline, strikethrough, note.

| To do this | Use |
|---|---|
| Highlight the selection | **Ctrl+Shift+H** (with nothing selected it picks the highlight tool) |
| Note on the selection | **Ctrl+Shift+N**. Write the note, optional tags separated by commas, **Ctrl+Enter** saves. |
| Bookmark this place | **Ctrl+D** (again to remove) |
| Sticky note | choose the sticky-note tool and click where it belongs |
| Draw | **P** pen, **N** pencil, **H** highlighter pen; **E** eraser removes a whole stroke; **M** selects and moves drawings; **V** returns to selecting text. Pen and mouse draw; finger touch scrolls. |
| Undo / redo | **Ctrl+Z**, **Ctrl+Y** - for every annotation change including deletes and moves |
| Delete | select it and press **Delete** (Ctrl+Z brings it back) |

The **Notes** tab lists every annotation. Filter by kind (All, Highlights, Notes, Bookmarks, Drawings), by tag, by text, and sort by position; clicking one takes you to it. The other tabs are Contents, Pages, Search and Assistant.

**Your marks stay attached to the words.** Reader remembers the quoted words and the text around them, not only a position. If the
document you opened changes, each annotation is checked again and shown as *in place*, *moved* (found elsewhere) or *detached* (the
words are gone). Detached ones are kept and listed so you can read them, re-attach or delete them; nothing is deleted for you. If a PDF is
regenerated and gets a new identity, Reader offers **Carry them over**.

**Not lost on a crash.** Every change is written to the database at once. Reader also keeps rolling backups (the first change, then
every 10 changes or 15 seconds) and a final one on a clean exit. If the database is damaged, Reader sets it aside, restores from the
latest backup and tells you. A hard power cut together with a damaged database can lose up to the last 15 seconds of changes. If a
second Reader window changed the same annotation, the later save is refused and the conflict is shown rather than overwritten.

## Taking your notes out

**Export** button in the toolbar (Export or import notes)

* *Notes as Markdown* - readable anywhere, in reading order with page or section references.
* *Notes as JSON* - complete, and importable back (Export dialog, **Import**) into another copy of Reader.
* *Notes as an SLDC document*.
* *This SLDC document with your notes as review comments* - a copy. Comments sit outside the signed content, so an existing signature stays valid. Drawings are not included.
* *PDF with standard annotations* - a copy with real highlight, underline, strike-out, note and ink annotations other PDF programs show and edit.
* *PDF with marks drawn in* - the marks are painted into the pages; always visible, not editable; text stays selectable.

Exports are new files in your output folder (`Documents\SLAI Reader`); an existing file is never overwritten. Encrypted PDFs are
not decrypted to make an export.

## The reading assistant (optional)

Open the **Assistant** tab in the side panel. **Nothing is sent to a SLAI agent until you tick "Let SLAI agents read this document"**, and the Privacy
agent still decides each time. The Safety agent looks at the text first. Then you can ask for a **summary** or for **related passages** to a question.

* Every answer says who produced it and lists the passages it is based on, with their places - click one to go there.
* A summary written by the language agent is checked against the document; if it uses words that are not in the text it is discarded and you are told.
* When an agent is missing, off, slow or refused, you get a plain fallback that is labelled as such: key sentences picked by word frequency
  (quoted, not rewritten), or passages ranked by matching words. Reader never makes up an answer.
* "Document check" sends only counts (words per page), never the text.
* Start Reader with `--agents none` to switch all of this off.

## Hand-over to other SLAI applications

**More > Open in Documaster or Writer...** writes a `slai-handoff/1` file with the document, your place and your selection. Reader can open such
a file (**--handoff FILE**). Today: Documaster is started (or found running) and shown the path, but it cannot yet read the file;
SLAI Writer is not part of this checkout, so "Writer" works only when you set `SLAI_WRITER_CMD`. Reader says which of these happened.

## Keyboard and accessibility

* Everything is reachable with the keyboard; **?** lists the shortcuts. Dialogs trap focus and give it back; popovers close with Escape.
* Screen reader names and roles are set on controls, panels, annotations and status messages (tested through Chromium's accessibility tree, not yet with NVDA or Narrator).
* Colour is never the only signal; high-contrast mode and Windows forced-colours mode are supported; reduced-motion is respected.
* The layout reflows down to 320 px wide (200 % zoom) without sideways scrolling; targets are at least 24 px.

## Limits you should know

* No OCR: scanned pages cannot be searched or highlighted as text.
* PDF text colours and sizes cannot be changed; document colours in DOCX/EPUB spans are not applied.
* Pagination of reflowable documents in the one-page layouts is approximate and differs from a printed layout.
* The eraser removes whole strokes. A drawing on a reflowable document belongs to the paragraph where it started.
* The side panel's library-wide search covers titles, notes, highlighted text and bookmarks, not the full text of every document.
* No shape tool yet (the stored format has room for one).
* The file you opened is never modified, so there are no marks inside it unless you export a PDF copy.
