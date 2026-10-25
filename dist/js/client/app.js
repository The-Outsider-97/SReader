// The application: the library page, an open document with its toolbar, side panel and status bar, keyboard commands, and the
// things that happen around a document (opening, password prompts, the file changing on disk, system colours).
import { ApiError } from '../core/api.js';
import { commandFor } from '../core/keys.js';
import { clampZoom, stepZoom } from '../core/zoom.js';
import { exportDialog, gotoDialog, handoffDialog, infoDialog, shortcutsDialog } from './dialogs.js';
import { announce, button, clear, h, replace } from './dom.js';
import { LibraryPage } from './library.js';
import { ReaderSession } from './reader.js';
import { openSettings } from './settings.js';
import { Sidebar } from './sidebar.js';
import { Toolbar } from './toolbar.js';
import { askPassword, closePopover, confirmDialog, dialog, popoverOpen, toast } from './ui.js';
export class App {
    root;
    state;
    session = null;
    docOnly = false;
    sidebar;
    toolbar = null;
    library = null;
    banners = h('div', { class: 'banners', attrs: { 'aria-live': 'polite' } });
    status = h('footer', { class: 'status' });
    statusParts = {};
    mq = window.matchMedia('(prefers-color-scheme: dark)');
    poll = 0;
    fileTimer = 0;
    constructor(root, state) {
        this.root = root;
        this.state = state;
        state.api.dark = () => this.mq.matches;
        this.sidebar = new Sidebar(state, () => this.toggleSide(false));
    }
    // ------------------------------------------------------------------ start
    async boot() {
        const api = this.state.api;
        try {
            const p = await api.get('/api/prefs');
            this.state.prefs = p.values;
            this.state.defaults = p.defaults;
            this.state.effective = p.effective;
            this.state.highlightColor = p.values.highlightColors[0] ?? '#facc15';
        }
        catch (e) {
            replace(this.root, h('main', { class: 'library' }, h('h1', { text: 'SLAI Reader' }), h('div', { class: 'banner bad', attrs: { role: 'alert' } }, h('div', { class: 'grow', text: e instanceof ApiError ? e.message : 'Reader could not be reached.' }), h('div', { class: 'small', text: 'Open the address Reader printed when it started, including the part after \u201C?token=\u201D.' }))));
            return;
        }
        this.applyTheme();
        this.mq.addEventListener('change', () => { void this.refreshEffective(); });
        document.addEventListener('keydown', (e) => this.key(e), true);
        document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible')
            void this.checkFile(); });
        this.fileTimer = window.setInterval(() => { void this.checkFile(); }, 15000);
        let notes = [];
        try {
            notes = (await api.get('/api/status')).startupNotes ?? [];
        }
        catch { /* informational */ }
        let startup = { opened: [], problems: [] };
        try {
            startup = await api.get('/api/startup');
        }
        catch { /* the library still works */ }
        if (startup.opened.length)
            await this.openPayload(startup.opened[0]);
        else
            await this.showLibrary();
        for (const n of notes)
            this.banner(n, 'info');
        for (const pr of startup.problems) {
            if (pr.error.code === 'password_required' || pr.error.code === 'wrong_password')
                void this.openPath(pr.path);
            else
                this.banner(`${pr.path}: ${pr.error.message}`, 'bad');
        }
        if (startup.opened.length > 1)
            this.banner(`${startup.opened.length - 1} more document${startup.opened.length === 2 ? ' was' : 's were'} named when Reader started. Find ${startup.opened.length === 2 ? 'it' : 'them'} under Recent documents.`, 'info');
    }
    applyTheme() {
        const e = this.state.effective;
        if (!e)
            return;
        document.documentElement.dataset.theme = e.theme;
        document.documentElement.classList.toggle('reduce-motion', this.state.prefs.reduceMotion);
        document.documentElement.style.colorScheme = e.colors.dark ? 'dark' : 'light';
    }
    async refreshEffective() {
        try {
            this.state.effective = this.session ? await this.state.api.put(`/api/doc/${this.session.sid}/prefs`, { values: {} }) : (await this.state.api.get('/api/prefs')).effective;
            this.applyTheme();
            this.session?.view.appearanceChanged();
            this.state.emit('prefs');
        }
        catch { /* keep the colours that are showing */ }
    }
    // ------------------------------------------------------------------ banners
    banner(text, kind = 'info', actions = []) {
        const el = h('div', { class: `banner ${kind}`, attrs: { role: kind === 'bad' ? 'alert' : 'status' } }, h('div', { class: 'grow', text }), ...actions.map((a) => button(a.label, () => { a.run(); el.remove(); }, { cls: 'small' })), button('Dismiss', () => el.remove(), { cls: 'small flat', icon: '\u2715', title: 'Dismiss' }));
        this.banners.appendChild(el);
        return el;
    }
    // ------------------------------------------------------------------ screens
    async showLibrary() {
        await this.closeSession();
        clear(this.root);
        this.toolbar = null;
        this.library = new LibraryPage(this);
        this.root.className = 'app library-mode';
        this.root.append(h('header', { class: 'toolbar' }, h('span', { class: 'title', text: 'SLAI Reader' }), h('span', { class: 'spacer' }), button('Appearance', () => { openSettings(this, document.querySelector('.toolbar button:last-child').getBoundingClientRect()); }, { icon: 'Aa', title: 'Reading mode and defaults' }), button('Shortcuts', () => shortcutsDialog(), { icon: '?', title: 'Keyboard shortcuts', keys: '?' })), h('div', { class: 'body' }, h('div', { class: 'main' }, this.banners, this.library.el)), h('div'));
        document.title = 'SLAI Reader';
    }
    async openPayload(p, jumpTo) {
        await this.closeSession();
        const session = new ReaderSession(this.state, p);
        this.session = session;
        this.state.effective = p.reading.effective;
        this.state.setTool('select');
        this.applyTheme();
        clear(this.root);
        clear(this.banners);
        this.root.className = 'app';
        this.toolbar = new Toolbar(this);
        this.sidebar.attach(session);
        this.state.side = this.state.prefs.sidebar;
        const main = h('div', { class: 'main' }, this.banners, session.view.el);
        this.buildStatus();
        this.root.append(this.toolbar.el, h('div', { class: 'body' }, this.sidebar.el, main), this.status);
        document.title = `${p.info.title || 'Document'} \u2013 SLAI Reader`;
        this.library = null;
        this.sidebar.render();
        this.state.on('annotations', () => session.view.annotationsChanged());
        await session.start();
        this.toolbar.update();
        this.updateStatus();
        this.noticesFor(p);
        this.classes();
        if (jumpTo) {
            const a = this.state.annotations.find((x) => x.id === jumpTo);
            if (a)
                await session.jumpTo(a);
        }
        session.view.el.focus({ preventScroll: true });
        announce(`Opened ${p.info.title || 'document'}. ${session.view.pageInfo().label}.`);
    }
    noticesFor(p) {
        for (const w of p.warnings)
            this.banner(w, 'warn');
        if (p.info.converted)
            this.banner(`Shown from a converted copy (${p.info.format.toUpperCase()}). The original file is not changed. Some layout may differ from the original.`, 'info');
        if (p.info.scan?.ocrNeeded)
            this.banner('This PDF is a scan: its pages are pictures without text. Reader does not run OCR, so text cannot be selected or searched. You can still draw, add sticky notes and bookmark pages.', 'warn');
        if (p.info.restrictions && !p.info.restrictions.copy)
            this.banner('This PDF does not allow copying text. Reader follows that and does not remove it.', 'info');
        if (p.carryOver) {
            this.banner(p.carryOver.message, 'warn', [{ label: 'Carry them over', run: () => { void this.carryOver(); } }]);
        }
        if (p.arrived) {
            for (const n of p.arrived.notes)
                this.banner(n, 'info');
            if (p.arrived.selection?.status && p.arrived.selection.status !== 'ok')
                this.banner('The selection that was sent with this document could not be found in it.', 'warn');
        }
        if (p.annotations.changedSinceAnnotated)
            this.banner(`${p.annotations.changedSinceAnnotated} annotation${p.annotations.changedSinceAnnotated === 1 ? ' was' : 's were'} made on an earlier version of this document. They were re-checked against its current text. See the Notes tab for any that could not be placed.`, 'info');
    }
    async carryOver() {
        const s = this.session;
        if (!s)
            return;
        try {
            await s.api.post(`/api/doc/${s.sid}/carry-over`, {});
            await s.refresh();
            toast('Annotations carried over.');
        }
        catch (e) {
            toast(e instanceof ApiError ? e.message : 'Could not carry them over.');
        }
    }
    classes() {
        const v = this.session?.view.el;
        if (!v)
            return;
        const t = this.state.tool;
        v.classList.toggle('inking', t === 'pen' || t === 'pencil' || t === 'highlighter' || t === 'eraser' || t === 'move');
        v.classList.toggle('tool-move', t === 'move');
        v.classList.toggle('tool-eraser', t === 'eraser');
        v.classList.toggle('tool-sticky', t === 'sticky');
        v.classList.toggle('drawing', t === 'pen' || t === 'pencil' || t === 'highlighter');
    }
    async closeSession() {
        const s = this.session;
        if (!s)
            return;
        this.session = null;
        this.state.doc = null;
        this.state.annotations = [];
        this.sidebar.attach(null);
        closePopover();
        await s.close();
    }
    // ------------------------------------------------------------------ opening things
    async openPath(path, password) {
        if (!path) {
            toast('Type the full path of a file, or use Browse.');
            return;
        }
        try {
            await this.openPayload(await this.state.api.post('/api/open', { path, password }));
        }
        catch (e) {
            await this.openFailed(e, (pw) => this.openPath(path, pw));
        }
    }
    async openLibrary(docId, jumpTo, password) {
        try {
            await this.openPayload(await this.state.api.post('/api/library/open', { docId, password }), jumpTo);
        }
        catch (e) {
            await this.openFailed(e, (pw) => this.openLibrary(docId, jumpTo, pw));
        }
    }
    async openFile(file, password) {
        try {
            toast(`Reading ${file.name}\u2026`, 1500);
            await this.openPayload(await this.state.api.upload(file.name, await file.arrayBuffer(), password));
        }
        catch (e) {
            await this.openFailed(e, (pw) => this.openFile(file, pw));
        }
    }
    async browse() {
        try {
            const res = await this.state.api.post('/api/pick', {});
            if ('sid' in res && res.picked)
                await this.openPayload(res);
            else if (res.unavailable)
                toast('The system file dialog is not available here. Type the path, or use \u201CChoose a file\u201D.');
        }
        catch (e) {
            await this.openFailed(e, () => Promise.resolve());
        }
    }
    async openFailed(e, retry) {
        if (e instanceof ApiError && (e.code === 'password_required' || e.code === 'wrong_password')) {
            const pw = await askPassword('This document is protected', e.code === 'wrong_password' ? 'Enter the password again.' : 'Enter the password to open it. Reader does not store it.', e.code === 'wrong_password' ? 'That password did not work.' : undefined);
            if (pw !== null)
                await retry(pw);
            return;
        }
        const message = e instanceof ApiError ? e.message : 'The document could not be opened.';
        if (this.library)
            this.library.showMessage(message, 'bad');
        else
            toast(message, 7000);
        announce(message, true);
    }
    openDialog() {
        return new Promise((resolve) => {
            const input = h('input', { attrs: { type: 'text', 'aria-label': 'Full path of the file', placeholder: 'C:\\Users\\you\\Documents\\paper.pdf', spellcheck: 'false', autocomplete: 'off' } });
            const file = h('input', { attrs: { type: 'file', 'aria-label': 'Choose a file to read' } });
            const dlg = dialog('Open a document', [h('div', { class: 'field' }, h('label', { text: 'Path', attrs: { for: (input.id = 'open-path') } }), input),
                h('div', { class: 'row' }, button('Browse\u2026', () => { dlg.close(); void this.browse(); }), file)], [{ label: 'Cancel' }, { label: 'Open', primary: true, onClick: () => { void this.openPath(input.value.trim()); } }], { onClose: resolve });
            file.addEventListener('change', () => { const f = file.files?.[0]; if (f) {
                dlg.close();
                void this.openFile(f);
            } });
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter') {
                e.preventDefault();
                dlg.close();
                void this.openPath(input.value.trim());
            } });
        });
    }
    // ------------------------------------------------------------------ the file changing on disk
    async checkFile() {
        const s = this.session;
        if (!s || document.visibilityState !== 'visible')
            return;
        try {
            const st = await s.api.get(`/api/doc/${s.sid}/file-status`);
            if (st.state === 'same' || st.state === 'unknown' || this.noticed === st.state)
                return;
            this.noticed = st.state;
            this.banner(st.message ?? 'The file changed.', st.state === 'missing' ? 'warn' : 'info', st.state === 'changed' ? [{ label: 'Reload', run: () => { void this.reload(); } }] : []);
        }
        catch { /* the service may be busy; check again later */ }
    }
    noticed = '';
    async reload(password) {
        const s = this.session;
        if (!s)
            return;
        try {
            await s.flushPosition(false);
            const p = await this.state.api.request('POST', `/api/doc/${s.sid}/reload`, {}, password ? { 'X-Reader-Password': password } : {});
            this.session = null;
            this.noticed = '';
            await s.close().catch(() => undefined);
            await this.openPayload(p);
        }
        catch (e) {
            await this.openFailed(e, (pw) => this.reload(pw));
        }
    }
    // ------------------------------------------------------------------ appearance
    async setAppearance(patch, docOnly) {
        const s = this.session;
        try {
            let eff;
            if (s && docOnly) {
                eff = await this.state.api.put(`/api/doc/${s.sid}/prefs`, { values: patch });
                s.docPrefs = { ...s.docPrefs, ...patch };
            }
            else {
                const res = await this.state.api.put('/api/prefs', patch);
                this.state.prefs = res.values;
                eff = res.effective;
                if (s) {
                    const overridden = Object.keys(patch).filter((k) => k in s.docPrefs);
                    eff = await this.state.api.put(`/api/doc/${s.sid}/prefs`, { values: {}, unset: overridden });
                    for (const k of overridden)
                        delete s.docPrefs[k];
                }
            }
            this.state.effective = eff;
            this.applyTheme();
            s?.view.appearanceChanged();
            this.state.emit('prefs');
            this.updateStatus();
        }
        catch (e) {
            toast(e instanceof ApiError ? e.message : 'That setting could not be saved.');
        }
    }
    async resetDocument() {
        const s = this.session;
        if (!s)
            return;
        this.state.effective = await this.state.api.put(`/api/doc/${s.sid}/prefs`, { reset: true });
        s.docPrefs = {};
        this.applyTheme();
        s.view.appearanceChanged();
        this.state.emit('prefs');
        toast('This document now uses your general settings.');
    }
    async resetEverything() {
        const res = await this.state.api.post('/api/prefs/reset', {});
        this.state.prefs = res.values;
        await this.resetDocument().catch(() => undefined);
        await this.refreshEffective();
    }
    openAppearance(anchor) { openSettings(this, anchor); }
    // ------------------------------------------------------------------ view commands
    zoomTo(percent) { void this.setAppearance({ zoomMode: 'custom', zoom: clampZoom(percent) }, true); }
    fit(mode) {
        if (mode === 'fit-page' && !this.session?.fixed && this.state.effective?.values.layout === 'continuous') {
            toast('Fit page applies to PDF pages and to paged text layouts.');
            return;
        }
        void this.setAppearance({ zoomMode: mode }, true);
    }
    setLayout(layout) { void this.setAppearance({ layout }, true); }
    toggleSide(force) {
        const open = force ?? this.state.side === 'none';
        const side = open ? (this.state.prefs.sidebar !== 'none' ? this.state.prefs.sidebar : 'outline') : 'none';
        this.state.prefs.sidebar = side;
        this.state.setSide(side);
        void this.state.api.put('/api/prefs', { sidebar: side }).catch(() => undefined);
    }
    toggleFocus() {
        this.state.focusMode = !this.state.focusMode;
        this.root.classList.toggle('focus-mode', this.state.focusMode);
        if (this.state.focusMode && !this.root.querySelector('.focus-exit'))
            this.root.append(button('Leave distraction-free mode', () => this.toggleFocus(), { cls: 'focus-exit', icon: '\u2715', keys: 'Esc' }));
        if (!this.state.focusMode)
            this.root.querySelector('.focus-exit')?.remove();
        window.setTimeout(() => this.session?.view.appearanceChanged(), 30);
        announce(this.state.focusMode ? 'Distraction-free reading on. Press Escape to leave.' : 'Distraction-free reading off.');
    }
    goto() { if (this.session)
        gotoDialog(this.session); }
    goPageNumber(text) {
        const s = this.session;
        if (!s)
            return;
        const n = Number(text.trim());
        const total = s.view.pageInfo().total;
        if (!Number.isInteger(n) || n < 1 || n > total) {
            toast(`Type a page between 1 and ${total}.`);
            return;
        }
        if (s.fixed)
            void s.view.goTo({ type: 'page', page: n - 1, y: 0, percent: (n - 1) / total });
        else
            gotoDialog(s);
    }
    exportDialog() { if (this.session)
        exportDialog(this.session); }
    noteOnSelection() { this.session?.ui.noteOnSelection(); }
    pickTool(t) {
        const s = this.session;
        if (!s)
            return;
        if ((t === 'highlight' || t === 'underline' || t === 'strike') && s.sel && this.state.tool !== t) {
            void s.markSelection(t);
            return;
        }
        this.state.setTool(this.state.tool === t && t !== 'select' ? 'select' : t);
        this.classes();
        if (t !== 'select')
            announce(`${t} tool on. Press Escape to stop.`);
    }
    moreMenu(anchor) {
        const s = this.session;
        if (!s || !this.toolbar)
            return;
        this.toolbar.openMore(anchor, [['About this document', () => infoDialog(s)], ['Open in Documaster or Writer\u2026', () => handoffDialog(s)], 'sep',
            ['Reload the file from disk', () => { void this.reload(); }], ['Keyboard shortcuts', () => shortcutsDialog()], 'sep', ['Close this document', () => { void this.showLibrary(); }]]);
    }
    // ------------------------------------------------------------------ status bar
    buildStatus() {
        const label = h('span', { class: 'num', attrs: { role: 'status' } });
        const progress = h('input', { class: 'prog', attrs: { type: 'range', min: 0, max: 1000, step: 1, 'aria-label': 'Reading progress' }, value: '0' });
        progress.addEventListener('change', () => { const s = this.session; if (s)
            void s.api.get(`/api/doc/${s.sid}/location?percent=${Number(progress.value) / 1000}`).then((l) => s.view.goTo(l, false)); });
        const pct = h('span', { class: 'num' });
        const zoom = h('span', { class: 'num' });
        const note = h('span', { class: 'clip' });
        this.statusParts = { label, progress, pct, zoom, note };
        replace(this.status, label, progress, pct, h('span', { class: 'spacer' }), note, zoom);
        this.state.on('status', () => this.updateStatus());
        this.state.on('annotations', () => this.updateStatus());
    }
    updateStatus() {
        const s = this.session;
        const p = this.statusParts;
        if (!s || !p.label)
            return;
        const info = s.view.pageInfo();
        p.label.textContent = info.label + (info.approximate ? ' (approximate)' : '');
        const loc = s.lastLoc ?? s.view.location();
        if (document.activeElement !== p.progress)
            p.progress.value = String(Math.round(loc.percent * 1000));
        p.progress.setAttribute('aria-valuetext', `${Math.round(loc.percent * 100)} percent`);
        p.pct.textContent = `${Math.round(loc.percent * 100)}%`;
        p.zoom.textContent = `${Math.round(s.view.zoomPercent())}%`;
        const n = this.state.annotations.length;
        p.note.textContent = `${n} annotation${n === 1 ? '' : 's'} \u00B7 saved as you go`;
    }
    // ------------------------------------------------------------------ keyboard
    key(e) {
        const t = e.target;
        const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
        const s = this.session;
        if (e.key === 'Escape') {
            if (popoverOpen())
                return;
            if (this.state.focusMode) {
                this.toggleFocus();
                e.preventDefault();
                return;
            }
            if (this.state.tool !== 'select') {
                this.state.setTool('select');
                this.classes();
                return;
            }
            if (s) {
                s.view.clearSelection();
                s.inkSelection([]);
                this.sidebar.clearSearch();
            }
            return;
        }
        if (!s) {
            if (!typing && e.key === '?') {
                shortcutsDialog();
                e.preventDefault();
            }
            return;
        }
        if (!typing && (e.key === 'Delete' || e.key === 'Backspace') && (this.state.inkSelected.size || this.state.selectedId)) {
            e.preventDefault();
            void s.deleteSelection();
            return;
        }
        const paged = (this.state.effective?.values.layout ?? 'continuous') === 'single' || this.state.effective?.values.layout === 'spread';
        if (!typing && paged && !e.ctrlKey && !e.metaKey && !e.altKey) {
            if (e.key === 'ArrowRight' || (e.key === ' ' && !e.shiftKey)) {
                s.view.step(1);
                e.preventDefault();
                return;
            }
            if (e.key === 'ArrowLeft' || (e.key === ' ' && e.shiftKey)) {
                s.view.step(-1);
                e.preventDefault();
                return;
            }
            if (e.key === 'Home') {
                s.view.edge('start');
                e.preventDefault();
                return;
            }
            if (e.key === 'End') {
                s.view.edge('end');
                e.preventDefault();
                return;
            }
        }
        const cmd = commandFor(e, typing);
        if (!cmd)
            return;
        const run = (fn) => { e.preventDefault(); e.stopPropagation(); fn(); };
        switch (cmd) {
            case 'search':
                run(() => this.sidebar.focusSearch());
                break;
            case 'goto':
                run(() => this.goto());
                break;
            case 'next':
                if (paged)
                    run(() => s.view.step(1));
                break;
            case 'prev':
                if (paged)
                    run(() => s.view.step(-1));
                break;
            case 'start':
                run(() => s.view.edge('start'));
                break;
            case 'end':
                run(() => s.view.edge('end'));
                break;
            case 'zoomIn':
                run(() => this.zoomTo(stepZoom(s.view.zoomPercent(), 1)));
                break;
            case 'zoomOut':
                run(() => this.zoomTo(stepZoom(s.view.zoomPercent(), -1)));
                break;
            case 'zoomReset':
                run(() => this.zoomTo(100));
                break;
            case 'fitWidth':
                run(() => this.fit('fit-width'));
                break;
            case 'fitPage':
                run(() => this.fit('fit-page'));
                break;
            case 'focus':
                run(() => this.toggleFocus());
                break;
            case 'fullscreen':
                run(() => { if (document.fullscreenElement)
                    void document.exitFullscreen();
                else
                    void document.documentElement.requestFullscreen().catch(() => toast('Full screen was not allowed by the browser.')); });
                break;
            case 'sidebar':
                run(() => this.toggleSide());
                break;
            case 'undo':
                if (!typing)
                    run(() => { void s.undo(); });
                break;
            case 'redo':
                if (!typing)
                    run(() => { void s.redo(); });
                break;
            case 'highlight':
                run(() => { if (s.sel)
                    void s.markSelection('highlight');
                else
                    this.pickTool('highlight'); });
                break;
            case 'note':
                run(() => this.noteOnSelection());
                break;
            case 'bookmark':
                run(() => { void s.toggleBookmark(); });
                break;
            case 'toolSelect':
                run(() => this.pickTool('select'));
                break;
            case 'toolPen':
                run(() => this.pickTool('pen'));
                break;
            case 'toolPencil':
                run(() => this.pickTool('pencil'));
                break;
            case 'toolHighlighter':
                run(() => this.pickTool('highlighter'));
                break;
            case 'toolEraser':
                run(() => this.pickTool('eraser'));
                break;
            case 'toolMove':
                run(() => this.pickTool('move'));
                break;
            case 'open':
                run(() => { void this.openDialog(); });
                break;
            case 'help':
                run(() => shortcutsDialog());
                break;
        }
    }
    async confirmLeave() { return confirmDialog('Leave this document?', 'Your annotations are already saved.', 'Leave'); }
}
//# sourceMappingURL=app.js.map