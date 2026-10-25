// The start page: recent documents, opening by path / dialog / drag-and-drop, and search across your notes.
import { ApiError, qs } from '../core/api.js';
import { formatBytes, percent, relativeTime, titleOf } from '../core/format.js';
import type { Annotation, DocPayload, LibraryRow } from '../core/types.js';
import type { App } from './app.js';
import { button, h, nextId, replace } from './dom.js';
import { confirmDialog, toast } from './ui.js';

interface FormatRow { id: string; label: string; extensions: string[]; layout: string }
interface Found { documents: Array<{ doc_id: string; title: string | null; path: string | null }>; annotations: Array<Annotation & { docTitle?: string | null }>; scope?: string }

export class LibraryPage {
  readonly el: HTMLElement;
  private rows: LibraryRow[] = [];
  private grid = h('div', { class: 'cards' });
  private found = h('div');
  private msg = h('div', { attrs: { role: 'status', 'aria-live': 'polite' } });
  private timer = 0;

  constructor(private readonly app: App) {
    this.el = h('div', { class: 'view libraryview', id: 'view', attrs: { tabindex: -1 } });
    void this.build();
  }

  private async build(): Promise<void> {
    const { app } = this;
    let formats: FormatRow[] = [];
    try { formats = (await app.state.api.get<{ formats: FormatRow[] }>('/api/status')).formats; } catch { /* the list is informational */ }
    const pathIn = h('input', { attrs: { type: 'text', placeholder: 'C:\\Users\\you\\Documents\\paper.pdf', 'aria-label': 'Full path of a file to open', autocomplete: 'off', spellcheck: 'false' }, style: { flex: '1', minWidth: '16rem' } });
    pathIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); void app.openPath(pathIn.value.trim()); } });
    const fileIn = h('input', { attrs: { type: 'file', 'aria-label': 'Choose a file to read', hidden: true } });
    fileIn.addEventListener('change', () => { const f = fileIn.files?.[0]; if (f) void app.openFile(f); fileIn.value = ''; });
    const drop = h('div', { class: 'drop', attrs: { role: 'group', 'aria-label': 'Open a document' } },
      h('p', {}, h('strong', { text: 'Drop a document here' }), ' or'),
      h('div', { class: 'row', style: { justifyContent: 'center' } },
        button('Browse\u2026', () => { void app.browse(); }, { cls: 'primary', title: 'Choose a file with the system file dialog' }),
        button('Choose a file', () => fileIn.click(), { title: 'Read a file through the browser (a private copy is kept by Reader)' })),
      h('div', { class: 'row', style: { justifyContent: 'center', marginTop: '10px' } }, pathIn, button('Open path', () => { void app.openPath(pathIn.value.trim()); })),
      h('p', { class: 'hint', text: formats.length ? `Reads ${formats.map((f) => f.label).join(', ')}. Your files are opened read-only; Reader never changes them.` : 'Your files are opened read-only; Reader never changes them.' }), fileIn);
    const stop = (e: DragEvent): void => { e.preventDefault(); };
    drop.addEventListener('dragenter', (e) => { stop(e); drop.classList.add('over'); });
    drop.addEventListener('dragover', (e) => { stop(e); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => { stop(e); drop.classList.remove('over'); const f = e.dataTransfer?.files?.[0]; if (f) void app.openFile(f); });

    const q = h('input', { attrs: { type: 'search', 'aria-label': 'Search titles, notes and highlights in all your documents', placeholder: 'Search your library and notes' }, style: { width: '100%' } });
    q.addEventListener('input', () => { window.clearTimeout(this.timer); this.timer = window.setTimeout(() => { void this.search(q.value); }, 300); });

    this.el.appendChild(h('main', { class: 'library' },
      h('div', { class: 'hero' }, h('h1', { text: 'SLAI Reader' }), h('p', { class: 'muted', text: 'Read, annotate and keep your place. Everything stays on this computer.' })),
      this.msg, drop,
      h('div', { class: 'field' }, h('label', { text: 'Search', attrs: { for: (q.id = nextId('q')) } }), q, h('div', { class: 'hint', text: 'Searches document titles, your notes, highlighted text and bookmarks. Search inside a document with Ctrl+F while reading it.' })), this.found,
      h('h2', { text: 'Recent documents' }), this.grid));
    await this.refresh();
  }

  async refresh(): Promise<void> {
    try {
      this.rows = await this.app.state.api.get<LibraryRow[]>('/api/library');
      this.paint();
    } catch (e) { replace(this.grid, h('p', { class: 'error', text: e instanceof ApiError ? e.message : 'The library could not be read.' })); }
  }

  private paint(): void {
    if (!this.rows.length) { replace(this.grid, h('p', { class: 'muted', text: 'Nothing here yet. Documents you open will be listed so you can pick up where you left off.' })); return; }
    replace(this.grid, ...this.rows.map((r) => this.card(r)));
  }

  private card(r: LibraryRow): HTMLElement {
    const gone = r.available === false;
    const card = h('article', { class: 'card' + (gone ? ' gone' : ''), attrs: { 'aria-label': titleOf(r) } });
    card.append(h('div', { class: 't clip', text: titleOf(r), title: r.path ?? '' }),
      h('div', { class: 'row small muted' }, h('span', { class: 'chip', text: (r.format ?? '').toUpperCase() || 'DOC' }), r.pages ? h('span', { text: `${r.pages} pages` }) : null,
        h('span', { text: r.opened ? `opened ${relativeTime(r.opened)}` : '' })),
      h('progress', { attrs: { max: 1, value: r.progress || 0, 'aria-label': `Read ${percent(r.progress)}` } }),
      h('div', { class: 'small muted', text: `${percent(r.progress)} read \u00B7 ${r.annotation_count} annotation${r.annotation_count === 1 ? '' : 's'} \u00B7 ${r.bookmark_count} bookmark${r.bookmark_count === 1 ? '' : 's'}` }),
      h('div', { class: 'small muted clip', text: r.managedCopy ? 'Opened by upload (a copy kept by Reader)' : r.path ?? '', title: r.path ?? '' }));
    if (gone) card.appendChild(h('div', { class: 'small warn', text: 'The file is not where it was. Your notes are kept; open the file again from its new place and they return.' }));
    const acts = h('div', { class: 'row' });
    acts.append(button(r.progress > 0.01 ? 'Continue' : 'Open', () => { void this.app.openLibrary(r.doc_id); }, { cls: 'primary small', disabled: gone }),
      button('Forget', () => { void this.forget(r); }, { cls: 'small', title: 'Remove from this list' }));
    card.appendChild(acts);
    return card;
  }

  private async forget(r: LibraryRow): Promise<void> {
    const n = r.annotation_count + r.bookmark_count;
    const ok = await confirmDialog('Remove from your library?', n ? `This removes \u201C${titleOf(r)}\u201D from the list. Your ${n} annotation${n === 1 ? '' : 's'} are kept and come back if you open the file again.` : `Remove \u201C${titleOf(r)}\u201D from the list?`, 'Remove');
    if (!ok) return;
    let del = false;
    if (n) del = await confirmDialog('Also delete the annotations?', 'Choose \u201CDelete them\u201D to erase this document\u2019s annotations for good. Choose \u201CKeep them\u201D to leave them stored.', 'Delete them', true);
    try { await this.app.state.api.post('/api/library/forget', { docId: r.doc_id, deleteAnnotations: del }); await this.refresh(); toast(del ? 'Removed, and its annotations were deleted.' : 'Removed from the list.'); }
    catch (e) { toast(e instanceof ApiError ? e.message : 'Could not remove it.'); }
  }

  private async search(text: string): Promise<void> {
    const q = text.trim();
    if (!q) { replace(this.found); return; }
    try {
      const res = await this.app.state.api.get<Found>(`/api/search${qs({ q })}`);
      const parts: HTMLElement[] = [];
      if (res.documents.length) parts.push(h('h3', { text: 'Documents' }), h('ul', { class: 'list' }, ...res.documents.map((d) => h('li', {}, h('button', { class: 'item', text: d.title ?? d.path ?? d.doc_id, attrs: { type: 'button' }, on: { click: () => { void this.app.openLibrary(d.doc_id); } } })))));
      if (res.annotations.length) parts.push(h('h3', { text: 'In your notes and highlights' }), h('ul', { class: 'list' }, ...res.annotations.slice(0, 100).map((a) => h('li', {}, h('button', { class: 'item', attrs: { type: 'button' }, on: { click: () => { void this.app.openLibrary(a.doc_id, a.id); } } },
        h('strong', { text: a.docTitle ?? 'Document' }), h('div', { class: 'small', text: [a.text, a.body.text, a.body.title].filter(Boolean).join(' \u2014 ').slice(0, 220) }))))));
      if (!parts.length) parts.push(h('p', { class: 'muted', text: 'No document title, note, highlight or bookmark matches.' }));
      replace(this.found, ...parts);
    } catch (e) { replace(this.found, h('p', { class: 'error', text: e instanceof ApiError ? e.message : 'Search failed.' })); }
  }

  showMessage(text: string, kind: 'info' | 'warn' | 'bad' = 'info'): void {
    replace(this.msg, text ? h('div', { class: `banner ${kind}`, attrs: { role: kind === 'bad' ? 'alert' : 'status' } }, h('div', { class: 'grow', text })) : null);
  }
}

export type { DocPayload };
export const bytes = formatBytes;
