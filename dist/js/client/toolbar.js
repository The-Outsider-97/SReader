// The toolbar for an open document. Built once per document; buttons are updated in place so keyboard focus is never lost.
import { clampZoom, stepZoom } from '../core/zoom.js';
import { button, h, nextId, setPressed } from './dom.js';
import { material, materialButton, changeMaterial } from './material.js';
import { closePopover, popover, swatches } from './ui.js';
import { handoffDialog, infoDialog, shortcutsDialog } from './dialogs.js';
const TOOLS = [
    { id: 'select', label: 'Select', icon: '↖', title: 'Select text', keys: 'V', needs: 'any' },
    { id: 'highlight', label: 'Highlight', icon: '▮', title: 'Highlight text', keys: 'Ctrl+Shift+H', needs: 'text' },
    { id: 'underline', label: 'Underline', icon: 'U̲', title: 'Underline text', needs: 'text' },
    { id: 'strike', label: 'Strike', icon: 'S̶', title: 'Strike through text', needs: 'text' },
    { id: 'sticky', label: 'Sticky', icon: '✎', title: 'Place a sticky note (click where it goes)', needs: 'ink' },
    { id: 'pen', label: 'Pen', icon: '✒', title: 'Pen', keys: 'P', needs: 'ink' },
    { id: 'pencil', label: 'Pencil', icon: '✏', title: 'Pencil', keys: 'N', needs: 'ink' },
    { id: 'highlighter', label: 'Marker', icon: '▬', title: 'Highlighter pen', keys: 'H', needs: 'ink' },
    { id: 'eraser', label: 'Eraser', icon: '⌫', title: 'Eraser: removes a whole stroke', keys: 'E', needs: 'ink' },
    { id: 'move', label: 'Move', icon: '✥', title: 'Select and move drawings', keys: 'M', needs: 'ink' },
];
const INK_COLORS = ['#e11d48', '#2563eb', '#16a34a', '#111827', '#f59e0b', '#a855f7'];
export class Toolbar {
    app;
    el;
    tools = new Map();
    zoomInput;
    pageInput;
    pageTotal;
    undoBtn;
    redoBtn;
    menuBtn;
    toolBtn;
    toolRow;
    toolsOpen = false;
    menuOpen = false;
    layoutSel;
    options;
    fitW;
    fitP;
    constructor(app) {
        this.app = app;
        this.el = h('header', { class: 'toolbar', attrs: { role: 'toolbar', 'aria-label': 'Reader tools' } });
        this.build();
        const s = app.state;
        s.on('tool', () => this.update());
        s.on('annotations', () => this.update());
        s.on('prefs', () => this.update());
        s.on('status', () => this.update());
        s.on('side', () => this.update());
    }
    build() {
        const { app } = this;
        const s = app.session;
        const cap = s.payload.capabilities;
        const g = (label, ...kids) => h('div', { class: 'group', attrs: { role: 'group', 'aria-label': label } }, ...kids.filter(Boolean));
        const action = (name, label, click, keys) => materialButton(name, label, click, { keys });
        const appearanceBtn = action('text_fields', 'Reading appearance', () => app.openAppearance(appearanceBtn.getBoundingClientRect()));
        this.toolBtn = action('pen', 'Show annotation tools', () => this.toggleTools());
        this.toolBtn.setAttribute('aria-controls', 'reader-annotation-toolbar');
        this.toolBtn.setAttribute('aria-expanded', 'false');
        this.menuBtn = action('menu', 'Open menu', () => this.toggleMenu());
        this.menuBtn.setAttribute('aria-controls', 'reader-actions-menu');
        this.menuBtn.setAttribute('aria-expanded', 'false');
        const title = s.payload.info.title || 'Untitled document';
        const identity = h('div', { class: 'reader-identity', attrs: { 'aria-label': 'Open document' } }, material('description'), h('span', { class: 'title clip', text: title, title }));
        const primary = h('div', { class: 'reader-primary' }, g('Document', action('home', 'Back to library', () => { void app.showLibrary(); }), action('folder_open', 'Open document', () => { void app.openDialog(); }, 'Ctrl+O'), identity), g('Pages', action('chevron_left', 'Previous page', () => s.view.step(-1), 'PageUp'), (this.pageInput = h('input', { class: 'zoomval', attrs: { type: 'text', 'aria-label': 'Go to page', inputmode: 'numeric' } })), (this.pageTotal = h('span', { class: 'muted num' })), action('chevron_right', 'Next page', () => s.view.step(1), 'PageDown')), g('Zoom', action('remove', 'Zoom out', () => app.zoomTo(stepZoom(s.view.zoomPercent(), -1)), 'Ctrl+-'), (this.zoomInput = h('input', { class: 'zoomval', attrs: { type: 'text', 'aria-label': 'Zoom percent (25 to 400)', inputmode: 'numeric' } })), action('add', 'Zoom in', () => app.zoomTo(stepZoom(s.view.zoomPercent(), 1)), 'Ctrl+='), (this.fitW = button('Fit width', () => app.fit('fit-width'), { cls: 'small fit-control', pressed: false })), (this.fitP = button('Fit page', () => app.fit('fit-page'), { cls: 'small fit-control', pressed: false, disabled: !s.fixed }))), g('Layout', (this.layoutSel = h('select', { attrs: { 'aria-label': 'Page layout' } }, ...[['continuous', 'Continuous'], ['single', 'Single page'], ['spread', 'Two-page spread']]
            .filter(([id]) => !cap.layouts || cap.layouts.includes(id))
            .map(([id, name]) => h('option', { value: id, text: name }))))), g('Actions', action('search', 'Search document', () => app.sidebar.focusSearch(), 'Ctrl+F'), appearanceBtn, this.toolBtn, this.menuBtn));
        this.layoutSel.addEventListener('change', () => app.setLayout(this.layoutSel.value));
        this.zoomInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') {
            e.preventDefault();
            const n = Number(this.zoomInput.value.replace('%', '').trim());
            if (Number.isFinite(n))
                app.zoomTo(clampZoom(n));
        } });
        this.zoomInput.addEventListener('blur', () => this.update());
        this.pageInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') {
            e.preventDefault();
            app.goPageNumber(this.pageInput.value);
        } });
        this.pageInput.addEventListener('blur', () => this.update());
        const tools = h('div', { class: 'group annotation-tools', attrs: { role: 'group', 'aria-label': 'Annotation tools' } });
        for (const t of TOOLS) {
            const b = button(t.label, () => app.pickTool(t.id), { icon: t.icon, title: t.title, keys: t.keys, pressed: t.id === 'select' });
            b.querySelector('.ico')?.replaceWith(material(t.id));
            this.tools.set(t.id, b);
            const why = this.unavailable(t.needs);
            if (why) {
                b.disabled = true;
                b.title = `${t.title}: ${why}`;
            }
            tools.appendChild(b);
        }
        const note = button('Note', () => app.noteOnSelection(), { icon: '▤', title: 'Add a note to selected text', disabled: !!this.unavailable('text') });
        note.querySelector('.ico')?.replaceWith(material('note'));
        tools.appendChild(note);
        this.undoBtn = button('Undo', () => { void s.undo(); }, { icon: '↶', title: 'Undo', keys: 'Ctrl+Z' });
        this.redoBtn = button('Redo', () => { void s.redo(); }, { icon: '↷', title: 'Redo', keys: 'Ctrl+Y' });
        this.undoBtn.querySelector('.ico')?.replaceWith(material('undo'));
        this.redoBtn.querySelector('.ico')?.replaceWith(material('redo'));
        this.options = h('div', { class: 'group', attrs: { 'aria-label': 'Tool options', role: 'group' } });
        this.toolRow = h('div', { class: 'reader-tools', id: 'reader-annotation-toolbar', hidden: true }, tools, g('History', this.undoBtn, this.redoBtn, action('bookmark', 'Bookmark this page', () => { void s.toggleBookmark(); }, 'Ctrl+D')), this.options);
        this.el.append(primary, this.toolRow);
        this.update();
    }
    toggleTools() {
        this.toolsOpen = !this.toolsOpen;
        this.toolRow.hidden = !this.toolsOpen;
        this.toolBtn.setAttribute('aria-expanded', String(this.toolsOpen));
        this.toolBtn.setAttribute('aria-label', this.toolsOpen ? 'Hide annotation tools' : 'Show annotation tools');
        setPressed(this.toolBtn, this.toolsOpen);
    }
    toggleMenu() {
        if (this.menuOpen) {
            closePopover();
            return;
        }
        const app = this.app;
        const s = app.session;
        if (!s)
            return;
        const menu = h('div', { class: 'menu reader-menu', id: 'reader-actions-menu', attrs: { role: 'menu', 'aria-label': 'Reader actions' } });
        const sections = [
            ['File', [['Open document', () => { void app.openDialog(); }], ['Export or import notes', () => app.exportDialog()],
                    ['Document information', () => infoDialog(s)],
                    ['Reload document', () => { void app.reload(); }], ['Close document', () => { void app.showLibrary(); }]]],
            ['View', [['Reading appearance', () => app.openAppearance(this.menuBtn.getBoundingClientRect())],
                    ['Distraction-free mode', () => app.toggleFocus()], ['Fit width', () => app.fit('fit-width')],
                    ['Fit page', () => app.fit('fit-page')], ['Reset zoom to 100%', () => app.zoomTo(100)]]],
            ['Tools', [['Annotation toolbar', () => this.toggleTools()], ['Keyboard shortcuts', () => shortcutsDialog()],
                    ['Open in Writer or Documaster', () => handoffDialog(s)]]],
        ];
        for (const [heading, items] of sections) {
            menu.appendChild(h('div', { class: 'reader-menu-heading', text: heading }));
            for (const [label, run] of items)
                menu.appendChild(h('button', { text: label, attrs: { role: 'menuitem', type: 'button' },
                    on: { click: () => { pop.close(); run(); } } }));
        }
        const pop = popover(this.menuBtn.getBoundingClientRect(), menu, {
            label: 'Reader menu', onClose: () => {
                this.menuOpen = false;
                this.menuBtn.setAttribute('aria-expanded', 'false');
                this.menuBtn.setAttribute('aria-label', 'Open menu');
                changeMaterial(this.menuBtn, 'menu');
            },
        });
        this.menuOpen = true;
        this.menuBtn.setAttribute('aria-expanded', 'true');
        this.menuBtn.setAttribute('aria-label', 'Close menu');
        changeMaterial(this.menuBtn, 'close');
        menu.querySelector('button')?.focus();
    }
    unavailable(needs) {
        const cap = this.app.session.payload.capabilities;
        if (needs === 'text' && !(cap.textSelection && cap.highlight))
            return 'this document has no selectable text (a scanned PDF?). Drawing, sticky notes and bookmarks still work.';
        if (needs === 'ink' && !cap.ink)
            return 'not available for this document.';
        return null;
    }
    update() {
        const { app } = this;
        const s = app.session;
        if (!s)
            return;
        const st = app.state;
        for (const [id, b] of this.tools)
            setPressed(b, st.tool === id);
        if (st.tool !== 'select' && !this.toolsOpen)
            this.toggleTools();
        this.undoBtn.disabled = !st.history.canUndo;
        this.undoBtn.title = st.history.undoLabel ? `Undo ${st.history.undoLabel} (Ctrl+Z)` : 'Nothing to undo';
        this.redoBtn.disabled = !st.history.canRedo;
        this.redoBtn.title = st.history.redoLabel ? `Redo ${st.history.redoLabel} (Ctrl+Y)` : 'Nothing to redo';
        if (document.activeElement !== this.zoomInput)
            this.zoomInput.value = `${Math.round(s.view.zoomPercent())}%`;
        const info = s.view.pageInfo();
        if (document.activeElement !== this.pageInput)
            this.pageInput.value = String(info.current);
        this.pageTotal.textContent = `of ${info.total}`;
        const v = st.effective.values;
        setPressed(this.fitW, v.zoomMode === 'fit-width');
        setPressed(this.fitP, v.zoomMode === 'fit-page');
        this.layoutSel.value = v.layout === 'focus' ? 'continuous' : v.layout;
        this.paintOptions();
    }
    lastOpts = '';
    paintOptions() {
        const st = this.app.state;
        const ink = st.inkTool;
        const key = `${st.tool}|${ink ? JSON.stringify(st.prefs.tool[ink]) : st.highlightColor}`;
        if (key === this.lastOpts)
            return;
        this.lastOpts = key;
        this.options.replaceChildren();
        if (ink)
            this.inkOptions(ink);
        else if (st.tool === 'highlight')
            this.options.append(h('span', { class: 'muted small', text: 'Colour' }), swatches(st.prefs.highlightColors, st.highlightColor, (c) => { st.highlightColor = c; this.lastOpts = ''; this.paintOptions(); }, 'Highlight colour'));
        else if (st.tool === 'eraser')
            this.options.append(h('span', { class: 'muted small', text: 'Drag over a stroke to remove all of it. Ctrl+Z brings it back.' }));
        else if (st.tool === 'move')
            this.options.append(h('span', { class: 'muted small', text: 'Click a stroke, then drag it. Shift-click adds to the selection; Delete removes it.' }));
        else if (st.tool === 'sticky')
            this.options.append(h('span', { class: 'muted small', text: 'Click on the page where the note should go.' }));
        else if (st.tool === 'pen' || st.tool === 'pencil' || st.tool === 'highlighter')
            this.inkOptions(st.tool);
    }
    inkOptions(tool) {
        const st = this.app.state;
        const cur = st.prefs.tool[tool];
        const save = (patch) => {
            st.prefs.tool = { ...st.prefs.tool, [tool]: { ...cur, ...patch } };
            void this.app.session?.api.put('/api/prefs', { tool: st.prefs.tool }).catch(() => undefined);
            this.lastOpts = '';
            this.paintOptions();
        };
        const custom = h('input', { attrs: { type: 'color', 'aria-label': 'Custom pen colour' }, value: cur.color });
        custom.addEventListener('change', () => save({ color: custom.value }));
        const w = h('input', { attrs: { type: 'range', min: 1, max: tool === 'highlighter' ? 40 : 20, step: 1, 'aria-label': 'Line thickness', id: nextId('w') }, value: String(cur.width) });
        w.addEventListener('change', () => save({ width: Number(w.value) }));
        this.options.append(swatches([...new Set([...INK_COLORS, cur.color])], cur.color, (c) => save({ color: c }), 'Pen colour'), custom, h('span', { class: 'muted small', text: 'Thickness' }), w);
    }
    openMore(anchor, items) {
        const menu = h('div', { class: 'menu', attrs: { role: 'menu' } });
        const pop = popover(anchor, menu, { label: 'More' });
        for (const it of items) {
            if (it === 'sep') {
                menu.appendChild(h('hr'));
                continue;
            }
            menu.appendChild(h('button', { text: it[0], attrs: { role: 'menuitem', type: 'button' }, on: { click: () => { pop.close(); it[1](); } } }));
        }
        menu.querySelector('button')?.focus();
    }
}
//# sourceMappingURL=toolbar.js.map