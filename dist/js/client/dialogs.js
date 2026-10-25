// Export / import, document information, go-to, shortcuts and hand-off to other SLAI applications.
import { ApiError } from '../core/api.js';
import { formatBytes, parsePageNumber, parsePercent } from '../core/format.js';
import { SHORTCUTS } from '../core/keys.js';
import { button, h, nextId } from './dom.js';
import { askPassword, dialog, toast } from './ui.js';
const KINDS = [
    { id: 'markdown', label: 'Notes as Markdown', help: 'Your highlights and notes in reading order, with page or section references. Readable anywhere.', show: () => true },
    { id: 'json', label: 'Notes as JSON (can be imported back)', help: 'Everything about your annotations, including drawings and anchors, so another copy of Reader can import them.', show: () => true },
    { id: 'sldc-notes', label: 'Notes as an SLDC document', help: 'A new SLDC document that lists your notes and quotes.', show: () => true },
    { id: 'sldc-comments', label: 'This document with your notes as review comments (SLDC)', help: 'A copy of the SLDC file with your highlights and notes added as comments. Comments are outside the signed content, so signatures stay valid. Drawings are not included.', show: (s) => s.payload.info.format === 'sldc' },
    { id: 'pdf-native', label: 'PDF with standard annotations', help: 'A copy of the PDF with real highlight, underline, strike-out, note and ink annotations that other PDF programs can show and edit.', show: (s) => s.fixed },
    { id: 'pdf-flatten', label: 'PDF with marks drawn in', help: 'A copy of the PDF with your marks drawn into the pages. Always visible, but not editable. Text stays selectable.', show: (s) => s.fixed },
];
export function describeReport(report) {
    const dl = h('dl', { class: 'small' });
    const add = (k, v) => {
        if (v === null || v === undefined || (Array.isArray(v) && !v.length))
            return;
        dl.appendChild(h('dt', { class: 'muted', text: k }));
        let text;
        if (Array.isArray(v))
            text = v.map((x) => (typeof x === 'object' && x ? Object.values(x).join(': ') : String(x))).join('\n');
        else if (typeof v === 'object')
            text = Object.entries(v).map(([a, b]) => `${a}: ${b}`).join(', ');
        else
            text = String(v);
        dl.appendChild(h('dd', { text, style: { whiteSpace: 'pre-wrap', margin: '0 0 6px' } }));
    };
    for (const [k, v] of Object.entries(report))
        add(k, v);
    return dl;
}
export function exportDialog(s) {
    const kinds = KINDS.filter((k) => k.show(s));
    const choice = h('select', { attrs: { 'aria-label': 'What to export' } }, ...kinds.map((k) => h('option', { value: k.id, text: k.label })));
    const help = h('p', { class: 'hint' });
    const sync = () => { help.textContent = kinds.find((k) => k.id === choice.value)?.help ?? ''; };
    choice.addEventListener('change', sync);
    sync();
    const counts = s.state.annotations.reduce((m, a) => ({ ...m, [a.kind]: (m[a.kind] ?? 0) + 1 }), {});
    const boxes = Object.keys(counts).sort().map((k) => {
        const id = nextId('k');
        const c = h('input', { attrs: { type: 'checkbox', id }, checked: true });
        return { k, c, el: h('span', { class: 'row' }, c, h('label', { text: `${k} (${counts[k]})`, attrs: { for: id } })) };
    });
    const result = h('div', { attrs: { 'aria-live': 'polite' } });
    const file = h('input', { attrs: { type: 'file', accept: '.json,application/json', 'aria-label': 'Choose a Reader notes file to import' } });
    const importResult = h('div', { attrs: { 'aria-live': 'polite' } });
    const doExport = async () => {
        const kind = choice.value;
        const picked = boxes.filter((b) => b.c.checked).map((b) => b.k);
        if (!picked.length) {
            toast('Choose at least one kind of annotation.');
            return false;
        }
        let password;
        result.textContent = 'Exporting…';
        try {
            if (kind === 'sldc-comments' && s.payload.info.restrictions?.encrypted) {
                const pw = await askPassword('Password needed', 'This document is encrypted. Enter its password so the copy can be encrypted the same way.');
                if (pw === null) {
                    result.textContent = '';
                    return false;
                }
                password = pw;
            }
            else if (kind === 'sldc-comments' && s.payload.info.encrypted) {
                const pw = await askPassword('Password needed', 'Enter the document password.');
                if (pw === null) {
                    result.textContent = '';
                    return false;
                }
                password = pw;
            }
            const res = await s.api.post(`/api/doc/${s.sid}/export`, { kind, kinds: picked.length === boxes.length ? undefined : picked, password });
            const link = h('a', { text: `Download ${res.name}`, attrs: { href: res.download, download: res.name } });
            result.replaceChildren(h('p', { class: 'ok', text: `${res.label}: ${res.count} annotation${res.count === 1 ? '' : 's'}.` }), h('p', {}, link), h('p', { class: 'small muted', text: `Also saved to: ${res.path}` }), describeReport(res.report ?? {}));
        }
        catch (e) {
            result.replaceChildren(h('p', { class: 'error', text: e instanceof ApiError ? e.message : 'The export failed.', attrs: { role: 'alert' } }));
        }
        return false;
    };
    const doImport = async () => {
        const f = file.files?.[0];
        if (!f) {
            toast('Choose a notes file first.');
            return;
        }
        try {
            const data = JSON.parse(await f.text());
            const res = await s.api.post(`/api/doc/${s.sid}/annotations/import`, data);
            s.state.setAnnotations(res);
            s.view.annotationsChanged();
            importResult.replaceChildren(h('p', { class: 'ok', text: `Imported ${res.imported}. ${res.duplicates} were already here.${res.skipped.length ? ` ${res.skipped.length} could not be placed:` : ''}` }), ...res.skipped.slice(0, 20).map((x) => h('div', { class: 'small', text: `• ${x.reason}` })));
        }
        catch (e) {
            importResult.replaceChildren(h('p', { class: 'error', text: e instanceof ApiError ? e.message : 'That file could not be read as a Reader notes file.', attrs: { role: 'alert' } }));
        }
    };
    dialog('Export and import notes', [
        h('div', { class: 'field' }, h('label', { text: 'Export', attrs: { for: (choice.id = nextId('x')) } }), choice, help),
        boxes.length ? h('div', { class: 'row', attrs: { role: 'group', 'aria-label': 'Include' } }, h('span', { class: 'muted', text: 'Include:' }), ...boxes.map((b) => b.el)) : h('p', { class: 'muted', text: 'There are no annotations yet.' }),
        h('div', { class: 'row', style: { margin: '10px 0' } }, button('Export', () => { void doExport(); }, { cls: 'primary', disabled: !boxes.length })), result,
        h('hr'),
        h('div', { class: 'field' }, h('label', { text: 'Import notes from a Reader JSON export', attrs: { for: (file.id = nextId('imp')) } }), file, h('div', { class: 'hint', text: 'Each note is re-attached to this document by its quoted text. Notes whose text cannot be found are listed, not placed somewhere else.' })),
        button('Import', () => { void doImport(); }), importResult,
    ], [{ label: 'Close', primary: true }], { wide: true });
}
export function infoDialog(s) {
    const i = s.payload.info;
    const rows = [
        ['Title', i.title], ['Format', i.format.toUpperCase() + (i.converted ? ' (converted for reading; the original is never changed)' : '')], ['Opened with', i.backend],
        ['Pages', i.pages ? String(i.pages) : null], ['Text blocks', i.blockCount !== undefined ? String(i.blockCount) : null], ['Characters', i.totalChars ? i.totalChars.toLocaleString() : null],
        ['Language', i.language || null], ['Revision', i.revision], ['Document id', s.payload.docId],
        ['Location', s.payload.path ?? (s.payload.managedCopy ? 'A copy Reader keeps (opened by upload)' : null)],
        ...Object.entries(i.metadata ?? {}).map(([k, v]) => [k, String(v)]),
    ];
    const body = [h('dl', {}, ...rows.filter((r) => r[1]).flatMap(([k, v]) => [h('dt', { class: 'muted small', text: k }), h('dd', { text: v, style: { margin: '0 0 6px', overflowWrap: 'anywhere' } })]))];
    const r = i.restrictions;
    if (r)
        body.push(h('p', { text: `Permissions: copy text ${r.copy ? 'allowed' : 'not allowed'}; annotate ${r.annotate ? 'allowed' : 'not allowed'}; print ${r.print ? 'allowed' : 'not allowed'}${r.encrypted ? '; encrypted' : ''}. Reader follows these and does not remove them.` }));
    if (i.scan?.ocrNeeded)
        body.push(h('p', { class: 'warn', text: 'This PDF is a scan: its pages are pictures without text. Reader does not run OCR, so text cannot be selected or searched. Drawing, sticky notes and bookmarks work.' }));
    if (i.signatures?.length)
        body.push(h('div', {}, h('strong', { text: 'Signatures' }), h('ul', {}, ...i.signatures.map((x) => h('li', { text: `${x.signer ?? x.id}: ${x.state}${x.problems?.length ? ' – ' + x.problems.join('; ') : ''}` })))));
    if (i.comments?.length)
        body.push(h('p', { text: `${i.comments.length} review comment thread${i.comments.length === 1 ? '' : 's'} in the document (shown read-only; your own notes are kept separately).` }));
    for (const w of [...i.warnings, ...(i.dropped ?? []).map((d) => `Not shown: ${d}`)])
        body.push(h('div', { class: 'banner warn' }, h('div', { class: 'grow', text: w })));
    dialog('About this document', body, [{ label: 'Close', primary: true }], { wide: true });
}
export function gotoDialog(s) {
    const input = h('input', { attrs: { type: 'text', 'aria-label': 'Page number or percentage', placeholder: s.fixed ? 'Page number, or a percentage such as 40%' : 'Page number or a percentage such as 40%' } });
    const total = s.view.pageInfo().total;
    const go = async () => {
        const text = input.value;
        const pct = parsePercent(text);
        if (pct !== null) {
            await s.view.goTo({ type: s.fixed ? 'page' : 'flow', page: Math.floor(pct * (s.payload.info.pages ?? 1)), y: 0, block: null, offset: 0, percent: pct });
            return true;
        }
        const n = parsePageNumber(text, total);
        if (n === null) {
            toast(`Type a page between 1 and ${total}, or a percentage like 40%.`);
            input.focus();
            return false;
        }
        if (s.fixed)
            await s.view.goTo({ type: 'page', page: n - 1, y: 0, percent: (n - 1) / total });
        else {
            const starts = s.payload.info.logicalPages ?? [];
            if (starts.length > 1)
                await s.view.goTo({ type: 'flow', block: null, n: starts[n - 1], offset: 0, percent: 0 });
            else {
                toast('This document has no pages; use a percentage like 40%.');
                return false;
            }
        }
        return true;
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') {
        e.preventDefault();
        input.closest('.dialog')?.querySelector('button.primary')?.click();
    } });
    dialog('Go to', [h('div', { class: 'field' }, h('label', { text: `Page (1–${total}) or percentage`, attrs: { for: (input.id = nextId('goto')) } }), input)], [{ label: 'Cancel' }, { label: 'Go', primary: true, onClick: go }]);
}
export function shortcutsDialog() {
    const groups = ['Navigation', 'View', 'Annotate', 'Document'];
    const body = groups.map((g) => h('div', {}, h('h3', { text: g }), h('table', { class: 'keys' }, ...SHORTCUTS.filter((x) => x.group === g).map((x) => h('tr', {}, h('td', {}, h('span', { class: 'kbd', text: x.keys })), h('td', { text: x.label }))))));
    dialog('Keyboard shortcuts', body, [{ label: 'Close', primary: true }], { wide: true });
}
export function handoffDialog(s) {
    const target = h('select', { attrs: { 'aria-label': 'Open in' } }, h('option', { value: 'documaster', text: 'Documaster' }), h('option', { value: 'writer', text: 'SLAI Writer' }));
    const withSel = h('input', { attrs: { type: 'checkbox' }, checked: !!s.sel, disabled: !s.sel });
    const sid = nextId('sel');
    withSel.id = sid;
    const out = h('div', { attrs: { 'aria-live': 'polite' } });
    const go = async () => {
        out.textContent = 'Sending…';
        try {
            const selection = withSel.checked && s.sel ? { segments: s.sel.segments } : undefined;
            const res = await s.api.post(`/api/doc/${s.sid}/handoff`, { target: target.value, position: s.view.location(), selection });
            out.replaceChildren(h('p', { class: 'ok', text: String(res.note ?? 'Done.') }), describeReport({ document: res.document, handoff: res.handoff }));
        }
        catch (e) {
            const details = e instanceof ApiError ? e.details : {};
            out.replaceChildren(h('p', { class: 'error', text: e instanceof ApiError ? e.message : 'The hand-off failed.', attrs: { role: 'alert' } }), details.handoff ? describeReport({ handoff: details.handoff, document: details.document }) : h('span'));
        }
        return false;
    };
    dialog('Open in another SLAI application', [
        h('p', { text: 'Reader writes a small hand-off file with this document, your place in it and (if you choose) the selected text, then starts the other application. Your annotations stay in Reader.' }),
        h('div', { class: 'field' }, h('label', { text: 'Open in', attrs: { for: (target.id = nextId('t')) } }), target),
        h('div', { class: 'row' }, withSel, h('label', { text: 'Include the selected text', attrs: { for: sid } })), out,
    ], [{ label: 'Close' }, { label: 'Send', primary: true, onClick: go }]);
}
export const sizeText = formatBytes;
//# sourceMappingURL=dialogs.js.map