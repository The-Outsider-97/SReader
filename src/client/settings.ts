// The appearance panel. Every control changes something that really changes the page, or is disabled with the reason.
import { readability } from '../core/appearance.js';
import type { Prefs } from '../core/types.js';
import { button, h, nextId } from './dom.js';
import type { ReaderSession } from './reader.js';
import type { AppState } from './state.js';
import { confirmDialog, popover, toast } from './ui.js';

export interface AppearanceHost {
  state: AppState;
  session: ReaderSession | null;
  setAppearance(patch: Partial<Prefs>, docOnly: boolean): Promise<void>;
  resetDocument(): Promise<void>;
  resetEverything(): Promise<void>;
  docOnly: boolean;
}

const THEMES: Array<[Prefs['theme'], string, string]> = [
  ['system', 'Follow system', 'Light or dark, whichever your computer uses'],
  ['light', 'Light', 'White paper, dark text'],
  ['sepia', 'Sepia', 'Warm paper, brown text; gentle for long reading'],
  ['dark', 'Dark', 'Dark grey paper, soft light text (not inverted)'],
  ['contrast', 'High contrast', 'Black and white with yellow links; maximum legibility'],
];

export function openSettings(app: AppearanceHost, anchor: DOMRect): void {
  const { state, session } = app;
  const v = state.effective!.values;
  const cap = session?.payload.capabilities;
  const fixed = session?.fixed ?? false;
  const docOnly = h('input', { attrs: { type: 'checkbox' }, checked: app.docOnly, disabled: !session });
  docOnly.addEventListener('change', () => { app.docOnly = docOnly.checked; });
  const dId = nextId('doc'); docOnly.id = dId;

  let timer = 0;
  const commit = (patch: Partial<Prefs>, immediate = false): void => {
    window.clearTimeout(timer);
    const go = (): void => { void app.setAppearance(patch, app.docOnly && !!session).then(refreshWarning); };
    if (immediate) go(); else timer = window.setTimeout(go, 180);
  };

  const row = (label: string, control: HTMLElement, note?: string, disabledWhy?: string): HTMLElement => {
    const id = control.id || (control.id = nextId('s'));
    const wrap = h('div', { class: 'field' }, h('label', { text: label, attrs: { for: id } }), control);
    if (disabledWhy) { (control as HTMLInputElement).disabled = true; wrap.appendChild(h('div', { class: 'hint', text: disabledWhy })); }
    else if (note) wrap.appendChild(h('div', { class: 'hint', text: note }));
    return wrap;
  };
  const slider = (min: number, max: number, step: number, value: number, fmt: (n: number) => string, patch: (n: number) => Partial<Prefs>, label: string, disabledWhy?: string, note?: string): HTMLElement => {
    const input = h('input', { attrs: { type: 'range', min, max, step }, value: String(value) });
    const out = h('span', { class: 'num muted', text: fmt(value), attrs: { 'aria-hidden': 'true' } });
    input.addEventListener('input', () => { out.textContent = fmt(Number(input.value)); input.setAttribute('aria-valuetext', fmt(Number(input.value))); commit(patch(Number(input.value))); });
    input.setAttribute('aria-valuetext', fmt(value));
    const wrap = h('div', { class: 'field' }, h('label', { attrs: { for: (input.id = nextId('s')) } }, label, ' ', out), input);
    if (disabledWhy) { input.disabled = true; wrap.appendChild(h('div', { class: 'hint', text: disabledWhy })); }
    else if (note) wrap.appendChild(h('div', { class: 'hint', text: note }));
    return wrap;
  };

  // theme
  const themeBox = h('div', { class: 'field', attrs: { role: 'radiogroup', 'aria-label': 'Reading mode' } }, h('div', { text: 'Reading mode', style: { fontWeight: '600', fontSize: '.85rem' } }));
  for (const [id, name, desc] of THEMES) {
    const rid = nextId('theme');
    const r = h('input', { attrs: { type: 'radio', name: 'theme', id: rid }, checked: v.theme === id });
    r.addEventListener('change', () => { if (r.checked) commit({ theme: id }, true); });
    themeBox.appendChild(h('div', { class: 'row' }, r, h('label', { attrs: { for: rid } }, h('strong', { text: name }), h('span', { class: 'muted small', text: ` – ${desc}` }))));
  }

  // text colour
  const colour = h('input', { attrs: { type: 'color' }, value: v.fontColor ?? state.effective!.colors.text });
  colour.addEventListener('input', () => commit({ fontColor: colour.value }));
  const themeColour = button('Use the mode’s colour', () => { commit({ fontColor: null }, true); colour.value = state.effective!.colors.text; }, { cls: 'small' });
  const warn = h('div', { class: 'banner warn', hidden: true, attrs: { role: 'status' } });
  function refreshWarning(): void {
    const e = state.effective!;
    const r = readability(e.values.fontColor, e.theme, e.values.fontOpacity);
    warn.hidden = r.level === 'ok';
    warn.textContent = r.message ?? '';
    warn.classList.toggle('bad', r.level === 'poor');
  }

  const layout = h('select', { attrs: { 'aria-label': 'Page layout' } },
    h('option', { value: 'continuous', text: 'Continuous scroll' }), h('option', { value: 'single', text: 'One page at a time' }), h('option', { value: 'spread', text: 'Two-page spread' }));
  layout.value = v.layout === 'focus' ? 'continuous' : v.layout;
  layout.addEventListener('change', () => commit({ layout: layout.value as Prefs['layout'] }, true));
  const cover = h('input', { attrs: { type: 'checkbox' }, checked: v.coverAlone });
  cover.addEventListener('change', () => commit({ coverAlone: cover.checked }, true));
  const cid = nextId('cover'); cover.id = cid;

  const family = h('select', {}, h('option', { value: 'serif', text: 'Serif (book)' }), h('option', { value: 'sans', text: 'Sans-serif' }), h('option', { value: 'mono', text: 'Monospace' }));
  family.value = v.fontFamily;
  family.addEventListener('change', () => commit({ fontFamily: family.value as Prefs['fontFamily'] }, true));
  const align = h('select', {}, h('option', { value: 'start', text: 'Left (ragged)' }), h('option', { value: 'justify', text: 'Justified' }));
  align.value = v.textAlign;
  align.addEventListener('change', () => commit({ textAlign: align.value as Prefs['textAlign'] }, true));
  const pdfMode = h('select', {}, h('option', { value: 'match', text: 'Match the reading mode' }), h('option', { value: 'original', text: 'As printed (original colours)' }));
  pdfMode.value = v.pdfAppearance;
  pdfMode.addEventListener('change', () => commit({ pdfAppearance: pdfMode.value as Prefs['pdfAppearance'] }, true));

  const flowOnly = fixed ? 'PDF pages are fixed layouts: their text size is part of the page. Use Zoom instead.' : undefined;
  const body = h('div', { class: 'settings', style: { width: 'min(420px, 92vw)', maxHeight: '75vh', overflow: 'auto' } },
    themeBox,
    h('div', { class: 'field' }, h('label', { text: 'Text colour', attrs: { for: (colour.id = nextId('s')) } }), h('div', { class: 'row' }, colour, themeColour), cap?.fontColorNote ? h('div', { class: 'hint', text: cap.fontColorNote }) : null),
    slider(30, 100, 5, v.fontOpacity, (n) => `${n}%`, (n) => ({ fontOpacity: n }), 'Text opacity', undefined, fixed ? cap?.fontColorNote : undefined),
    warn,
    slider(10, 48, 1, v.fontSize, (n) => `${n}px`, (n) => ({ fontSize: n }), 'Text size', flowOnly),
    row('Typeface', family, undefined, flowOnly),
    slider(1, 2.5, 0.1, v.lineSpacing, (n) => n.toFixed(1), (n) => ({ lineSpacing: Math.round(n * 10) / 10 }), 'Line spacing', flowOnly),
    slider(30, 140, 5, v.columnWidth, (n) => `${n} characters`, (n) => ({ columnWidth: n }), 'Line length', flowOnly),
    row('Alignment', align, undefined, flowOnly),
    ...(fixed ? [row('PDF colours', pdfMode, 'Recolours the page when it is drawn. Scanned images keep their own colours.')] : []),
    row('Page layout', layout),
    h('div', { class: 'row' }, cover, h('label', { text: 'In a two-page spread, show the cover on its own', attrs: { for: cid } })),
    h('hr'),
    h('div', { class: 'row' }, docOnly, h('label', { text: 'Apply these changes to this document only', attrs: { for: dId } })),
    h('div', { class: 'row', style: { marginTop: '10px' } },
      button('Reset this document', () => { void app.resetDocument().then(() => pop.close()); }, { cls: 'small', disabled: !session }),
      button('Reset everything', () => { void confirmDialog('Reset all settings?', 'This puts every reading setting back to its default, for all documents.', 'Reset', true).then((ok) => { if (ok) { void app.resetEverything().then(() => { pop.close(); toast('Settings reset.'); }); } }); }, { cls: 'small danger' })));
  const pop = popover(anchor, body, { label: 'Appearance' });
  refreshWarning();
}
