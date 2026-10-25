// The small panels for annotating: what appears next to a selection, the note editor, the editor for an existing annotation,
// sticky notes and references. Nothing here stores anything itself; it asks the ReaderSession, which asks the service.
import { parseTags } from '../core/format.js';
import { button, h, labelled, nextId } from './dom.js';
import { closePopover, popover, swatches, toast } from './ui.js';
const STICKY_COLORS = ['#fde047', '#fda4af', '#86efac', '#93c5fd', '#fdba74'];
const LINE_COLORS = ['#ef4444', '#2563eb', '#16a34a', '#111827', '#a855f7'];
const KIND_LABEL = { highlight: 'Highlight', underline: 'Underline', strike: 'Strikethrough', note: 'Note', sticky: 'Sticky note', ink: 'Drawing', shape: 'Shape', bookmark: 'Bookmark' };
export class AnnotateUi {
    r;
    tools = null;
    editor = null;
    constructor(r) {
        this.r = r;
    }
    dispose() { closePopover(); this.tools = null; this.editor = null; }
    // ------------------------------------------------------------------ next to a selection
    selectionChanged(info) {
        if (this.editor)
            return; // an editor is open: leave it alone
        if (!info) {
            this.tools?.close();
            this.tools = null;
            return;
        }
        if (!info.final) {
            this.tools?.close();
            this.tools = null;
            return;
        }
        const tool = this.r.state.tool;
        if (tool === 'highlight' || tool === 'underline' || tool === 'strike') {
            void this.r.markSelection(tool);
            return;
        }
        if (this.r.state.inkTool || tool === 'eraser' || tool === 'move')
            return;
        this.showTools(info);
    }
    showTools(info = this.r.sel) {
        if (!info)
            return;
        const { state } = this.r;
        const colors = state.prefs.highlightColors;
        const custom = h('input', { attrs: { type: 'color', 'aria-label': 'Custom highlight colour', title: 'Custom colour' }, value: state.highlightColor });
        custom.addEventListener('change', () => { this.rememberColor(custom.value); void this.r.markSelection('highlight', custom.value); });
        const row = h('div', { class: 'seltools', attrs: { role: 'toolbar', 'aria-label': 'Selected text' } }, swatches(colors, null, (c) => { state.highlightColor = c; void this.r.markSelection('highlight', c); }, 'Highlight colour'), custom, button('', () => { void this.r.markSelection('underline'); }, { icon: 'U̲', title: 'Underline', cls: 'flat' }), button('', () => { void this.r.markSelection('strike'); }, { icon: 'S̶', title: 'Strikethrough', cls: 'flat' }), button('Note', () => this.noteOnSelection(info), { icon: '✎', cls: 'flat', title: 'Add a note to the selection' }), button('', () => { void this.copy(); }, { icon: '⎘', title: 'Copy text', cls: 'flat', disabled: !this.r.canCopy() }), button('', () => { state.setSide('search'); state.emit('view'); this.r.state.pendingSearch = this.r.view.selectedText().slice(0, 200); state.emit('side'); }, { icon: '⌕', title: 'Search for this text', cls: 'flat' }));
        this.tools?.close();
        this.tools = popover(info.rect, row, { label: 'Selected text', keepFocus: true, cls: 'seltoolspop', onClose: () => { this.tools = null; } });
    }
    async copy() {
        const text = this.r.view.selectedText();
        try {
            await navigator.clipboard.writeText(text);
            toast('Copied.');
        }
        catch {
            toast('Copying was not allowed by the browser. Use Ctrl+C.');
        }
        this.tools?.close();
    }
    rememberColor(c) {
        const recent = [c, ...this.r.state.prefs.recentColors.filter((x) => x !== c)].slice(0, 8);
        this.r.state.prefs.recentColors = recent;
        void this.r.api.put('/api/prefs', { recentColors: recent }).catch(() => undefined);
    }
    // ------------------------------------------------------------------ a note on selected text
    noteOnSelection(info = this.r.sel) {
        if (!info) {
            toast('Select some text first.');
            return;
        }
        this.tools?.close();
        this.tools = null;
        let color = this.r.state.highlightColor;
        const text = h('textarea', { attrs: { rows: 4, maxlength: 20000 } });
        const tags = h('input', { attrs: { type: 'text', placeholder: 'tag, another tag' } });
        const also = h('input', { attrs: { type: 'checkbox' }, checked: true });
        const alsoId = nextId('also');
        also.id = alsoId;
        const quote = h('div', { class: 'quote', text: info.text.length > 400 ? info.text.slice(0, 400) + '…' : info.text });
        const save = () => {
            const body = { text: text.value };
            const t = parseTags(tags.value);
            if (t.length)
                body.tags = t;
            if (!text.value.trim()) {
                toast('Write something first, or close this to leave the text unmarked.');
                text.focus();
                return;
            }
            const hl = also.checked;
            void this.r.create([{ kind: hl ? 'highlight' : 'note', anchor: this.r.textAnchor(info.segments), body, style: { color } }]).then((made) => {
                if (made?.length) {
                    this.r.view.clearSelection();
                    this.r.selection(null);
                }
            });
            this.editor?.close();
        };
        const content = h('div', { class: 'noteedit' }, quote, labelled('Note', text), labelled('Tags (separate with commas)', tags), h('div', { class: 'row' }, also, h('label', { text: 'Also highlight the text', attrs: { for: alsoId } })), swatches(this.r.state.prefs.highlightColors, color, (c) => { color = c; this.r.state.highlightColor = c; content.querySelectorAll('.swatch').forEach((s) => { const on = s.title === c; s.classList.toggle('current', on); s.setAttribute('aria-pressed', String(on)); }); }, 'Colour'), h('div', { class: 'row', style: { marginTop: '10px' } }, button('Save note', save, { cls: 'primary' }), button('Cancel', () => this.editor?.close()), h('span', { class: 'hint', text: 'Ctrl+Enter saves' })));
        content.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            save();
        } }); // from the note or the tags field
        this.editor = popover(info.rect, content, { label: 'Add a note', onClose: () => { this.editor = null; } });
    }
    // ------------------------------------------------------------------ sticky notes
    newSticky(anchor, near) {
        let color = STICKY_COLORS[0];
        const text = h('textarea', { attrs: { rows: 4, maxlength: 20000, 'aria-label': 'Sticky note text' } });
        const save = () => {
            if (!text.value.trim()) {
                toast('Write something in the note first.');
                text.focus();
                return;
            }
            void this.r.create([{ kind: 'sticky', anchor, body: { text: text.value }, style: { color } }], 'Add sticky note').then((m) => { if (m?.length) {
                this.r.state.setTool('select');
                this.r.state.selectedId = m[0].id;
                this.r.state.emit('annotations');
            } });
            this.editor?.close();
        };
        text.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            save();
        } });
        const sw = swatches(STICKY_COLORS, color, (c) => { color = c; sw.querySelectorAll('.swatch').forEach((s) => { const on = s.title === c; s.classList.toggle('current', on); s.setAttribute('aria-pressed', String(on)); }); }, 'Sticky note colour');
        const content = h('div', { class: 'noteedit' }, labelled('Sticky note', text), sw, h('div', { class: 'row', style: { marginTop: '10px' } }, button('Add note', save, { cls: 'primary' }), button('Cancel', () => this.editor?.close())));
        this.editor?.close();
        this.editor = popover(near, content, { label: 'New sticky note', onClose: () => { this.editor = null; } });
    }
    // ------------------------------------------------------------------ an existing annotation
    editAnnotation(id, near) {
        const a = this.r.state.annotations.find((x) => x.id === id);
        if (!a)
            return;
        this.tools?.close();
        this.tools = null;
        this.editor?.close();
        this.r.state.selectedId = id;
        this.r.state.emit('annotations');
        this.r.view.annotationsChanged();
        const content = this.annotationForm(a);
        this.editor = popover(near, content, { label: `${KIND_LABEL[a.kind] ?? 'Annotation'}`, onClose: () => { this.editor = null; if (this.r.state.selectedId === id) {
                this.r.state.selectedId = null;
                this.r.state.emit('annotations');
                this.r.view.annotationsChanged();
            } } });
    }
    annotationForm(a) {
        const r = this.r;
        const dirty = { text: false };
        const parts = [];
        const head = h('div', { class: 'row' }, h('strong', { text: KIND_LABEL[a.kind] ?? a.kind }), a.status === 'moved' ? h('span', { class: 'chip warn', text: 'moved', title: 'The text changed a little; the mark follows it' }) : null, a.status === 'detached' ? h('span', { class: 'chip warn', text: 'text not found' }) : null, h('span', { class: 'spacer' }), h('span', { class: 'muted small', text: new Date(a.modified).toLocaleString() }));
        parts.push(head);
        if (a.text)
            parts.push(h('div', { class: 'quote', text: a.text.length > 300 ? a.text.slice(0, 300) + '…' : a.text }));
        const palette = a.kind === 'sticky' ? STICKY_COLORS : a.kind === 'underline' || a.kind === 'strike' ? LINE_COLORS : a.kind === 'ink' ? [...new Set([a.style.color, ...LINE_COLORS])] : r.state.prefs.highlightColors;
        if (a.kind !== 'bookmark') {
            const pick = (c) => { void r.patch(a.id, { style: { color: c } }, 'Change colour'); };
            const custom = h('input', { attrs: { type: 'color', 'aria-label': 'Custom colour' }, value: a.style.color });
            custom.addEventListener('change', () => pick(custom.value));
            parts.push(h('div', { class: 'row' }, swatches(palette, a.style.color, pick, 'Colour'), custom));
        }
        if (a.kind === 'highlight' || a.kind === 'ink') {
            const op = h('input', { attrs: { type: 'range', min: 5, max: 100, step: 5 }, value: String(Math.round(a.style.opacity * 100)) });
            op.addEventListener('change', () => { void r.patch(a.id, { style: { opacity: Number(op.value) / 100 } }, 'Change opacity'); });
            parts.push(labelled(`Opacity (${Math.round(a.style.opacity * 100)}%)`, op));
        }
        if (a.kind === 'ink') {
            const w = h('input', { attrs: { type: 'range', min: 1, max: 40, step: 1 }, value: String(a.style.width ?? 3) });
            w.addEventListener('change', () => { void r.patch(a.id, { style: { width: Number(w.value) } }, 'Change thickness'); });
            parts.push(labelled(`Thickness (${a.style.width ?? 3})`, w));
        }
        let title = null;
        if (a.kind === 'bookmark') {
            title = h('input', { attrs: { type: 'text', maxlength: 200 }, value: a.body.title ?? '' });
            title.addEventListener('input', () => { dirty.text = true; });
            parts.push(labelled('Name', title));
        }
        let text = null;
        let tags = null;
        if (a.kind !== 'ink' && a.kind !== 'shape') {
            text = h('textarea', { attrs: { rows: 4, maxlength: 20000 }, value: a.body.text ?? '' });
            text.addEventListener('input', () => { dirty.text = true; });
            tags = h('input', { attrs: { type: 'text', placeholder: 'tag, another tag' }, value: (a.body.tags ?? []).join(', ') });
            tags.addEventListener('input', () => { dirty.text = true; });
            parts.push(labelled(a.kind === 'bookmark' ? 'Note' : 'Note on this', text), labelled('Tags', tags));
        }
        const save = async () => {
            if (!dirty.text)
                return;
            dirty.text = false;
            const body = {};
            if (text)
                body.text = text.value;
            if (title)
                body.title = title.value;
            if (tags)
                body.tags = parseTags(tags.value);
            if (a.kind === 'note' && text && !text.value.trim()) {
                toast('A note needs some text. Delete it to remove it.');
                return;
            }
            await r.patch(a.id, { body }, 'Edit note');
        };
        const content = h('div', { class: 'noteedit' }, ...parts.filter(Boolean), h('div', { class: 'row', style: { marginTop: '10px' } }, button('Save', () => { void save().then(() => this.editor?.close()); }, { cls: 'primary' }), button('Delete', () => { void r.remove([a.id]); this.editor?.close(); toast('Deleted. Press Ctrl+Z to undo.'); }, { cls: 'danger' }), button('Show in list', () => { r.state.setSide('annotations'); this.editor?.close(); }, { cls: 'flat' })));
        // Anything typed is kept when the panel is closed any other way.
        content.addEventListener('focusout', () => { if (dirty.text)
            void save(); });
        return content;
    }
    // ------------------------------------------------------------------ references (footnotes and citations)
    showReference(id, near) {
        const ref = this.r.payload.info.references?.[id];
        const body = h('div', { class: 'noteedit' });
        if (!ref)
            body.appendChild(h('p', { class: 'muted', text: 'This reference is not listed in the document.' }));
        else {
            body.appendChild(h('strong', { text: ref.kind === 'footnote' ? 'Footnote' : 'Citation' }));
            const dl = h('dl');
            for (const [k, v] of Object.entries(ref)) {
                if (k === 'id' || k === 'kind' || v === null || v === undefined)
                    continue;
                dl.appendChild(h('dt', { class: 'muted small', text: k }));
                dl.appendChild(h('dd', { text: Array.isArray(v) ? v.join(', ') : String(v) }));
            }
            body.appendChild(dl);
        }
        popover(near, body, { label: 'Reference' });
    }
}
//# sourceMappingURL=annotate-ui.js.map