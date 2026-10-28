// The appearance panel. Every control changes something that really changes the page, or is disabled with the reason.
// The panel keeps no settings of its own: it shows what the service has confirmed (`state.effective`), sends changes through the
// app's one batcher, and says plainly whether they were saved.
import { readability } from '../core/appearance.js';
import type { PatchBatcher } from '../core/commit.js';
import type { Prefs } from '../core/types.js';
import { field, section, segmented, slider, toggle } from './controls.js';
import { button, h, nextId } from './dom.js';
import { material } from './material.js';
import type { ReaderSession } from './reader.js';
import type { AppState } from './state.js';
import { popover } from './ui.js';

export interface AppearanceHost {
  state: AppState;
  session: ReaderSession | null;
  docOnly: boolean;
  panelOpen: boolean;
  appearance: PatchBatcher;
  commitAppearance(patch: Partial<Prefs>, immediate?: boolean): void;
  resetDocument(): Promise<void>;
  resetEverything(): Promise<void>;
}

const THEMES: Array<[Prefs['theme'], string, string]> = [
  ['system', 'Follow system', 'Light or dark, whichever your computer uses'],
  ['light', 'Light', 'White paper, dark text'],
  ['sepia', 'Sepia', 'Warm paper, brown text; gentle for long reading'],
  ['dark', 'Dark', 'Dark grey paper, soft light text (not inverted)'],
  ['contrast', 'High contrast', 'Black and white with yellow links; maximum legibility'],
];
const SWATCH: Record<string, [string, string]> = { system: ['#ffffff', '#161b22'], light: ['#ffffff', '#1a1a1a'], sepia: ['#f4ecd8', '#433422'], dark: ['#161b22', '#dde1e7'], contrast: ['#000000', '#ffff00'] };

export function openSettings(app: AppearanceHost, anchor: DOMRect): void {
  const session = app.session;                           // the panel belongs to this document, even if another is opened later
  const cap = session?.payload.capabilities;
  const fixed = session?.fixed ?? false;
  const batch = app.appearance;
  const state = app.state;
  const sets: Array<{ keys: string[]; sync: (v: Prefs) => void }> = [];
  const commit = (patch: Partial<Prefs>, immediate = false): void => app.commitAppearance(patch, immediate);

  // ---- reading mode
  const themeGroup = h('div', { class: 'modes', attrs: { role: 'radiogroup', 'aria-label': 'Reading mode' } });
  const themeInputs = new Map<string, HTMLInputElement>();
  for (const [id, name, desc] of THEMES) {
    const rid = nextId('theme');
    const input = h('input', { class: 'visually-hidden', attrs: { type: 'radio', name: 'reading-mode', id: rid, 'aria-describedby': `${rid}-d` } });
    input.addEventListener('change', () => { if (input.checked) commit({ theme: id }, true); });
    themeInputs.set(id, input);
    const [paper, ink] = SWATCH[id];
    const chip = h('span', { class: `chip-mode${id === 'system' ? ' split' : ''}`, attrs: { 'aria-hidden': 'true' } },
      ...(id === 'system' ? [h('i', { text: 'A' }), h('i', { text: 'a' })] : [h('i', { text: 'Aa' })]));
    chip.style.setProperty('--p', paper); chip.style.setProperty('--i', ink);
    themeGroup.append(input, h('label', { class: 'mode', attrs: { for: rid } }, chip, h('strong', { text: name }), h('span', { class: 'visually-hidden', id: `${rid}-d`, text: desc })));
    themeGroup.lastElementChild?.setAttribute('title', desc);
  }
  sets.push({ keys: ['theme'], sync: (v) => { const i = themeInputs.get(v.theme); if (i) i.checked = true; } });

  // ---- text
  const colour = h('input', { attrs: { type: 'color', 'aria-label': 'Text colour' } });
  colour.addEventListener('input', () => commit({ fontColor: colour.value }));
  const modeColour = button('Use the mode’s colour', () => commit({ fontColor: null }, true), { cls: 'small' });
  const colourField = field('Text colour', h('div', { class: 'row' }, colour, modeColour));
  colourField.control.removeAttribute('id');
  colourField.el.querySelector('label')?.setAttribute('for', (colour.id = nextId('c')));
  const colourDisabled = cap && cap.fontColor === false ? (cap.fontColorNote ?? 'This document keeps its own text colour.') : null;
  if (colourDisabled) { colour.disabled = true; modeColour.disabled = true; colourField.el.classList.add('is-disabled'); }
  const colourHint = h('div', { class: 'hint', text: colourDisabled ?? cap?.fontColorNote ?? '', hidden: !(colourDisabled ?? cap?.fontColorNote) });
  colourField.el.append(colourHint);
  sets.push({ keys: ['fontColor'], sync: () => { colour.value = state.effective!.values.fontColor ?? state.effective!.colors.text; } });

  const opacity = slider('Text opacity', { min: 30, max: 100, step: 5, value: 100, format: (n) => `${n}%`, onInput: (n) => commit({ fontOpacity: n }) });
  if (cap && cap.fontOpacity === false) opacity.setDisabled(cap.fontColorNote ?? 'This document keeps its own text colour.');
  sets.push({ keys: ['fontOpacity'], sync: (v) => opacity.set(v.fontOpacity) });
  const warn = h('div', { class: 'banner warn', hidden: true, attrs: { role: 'status' } });

  const reasonFor = (flag: 'fontSize' | 'fontFamily' | 'lineSpacing' | 'columnWidth' | 'textAlign'): string | null =>
    cap && cap[flag] === false ? (cap.fontSizeNote ?? 'This document’s text layout is fixed. Use Zoom instead.') : null;
  const size = slider('Text size', { min: 10, max: 48, step: 1, value: 18, format: (n) => `${n}px`, onInput: (n) => commit({ fontSize: n }) });
  const sizeWhy = reasonFor('fontSize'); if (sizeWhy) size.setDisabled(sizeWhy);
  sets.push({ keys: ['fontSize'], sync: (v) => size.set(v.fontSize) });

  const family = h('select', {}, h('option', { value: 'serif', text: 'Serif (book)' }), h('option', { value: 'sans', text: 'Sans-serif' }), h('option', { value: 'mono', text: 'Monospace' }));
  family.addEventListener('change', () => commit({ fontFamily: family.value as Prefs['fontFamily'] }, true));
  const familyField = field('Typeface', family);
  const familyWhy = reasonFor('fontFamily'); if (familyWhy) familyField.setDisabled(familyWhy);
  sets.push({ keys: ['fontFamily'], sync: (v) => { family.value = v.fontFamily; } });

  const spacing = slider('Line spacing', { min: 1, max: 2.5, step: 0.1, value: 1.6, format: (n) => n.toFixed(1), onInput: (n) => commit({ lineSpacing: Math.round(n * 10) / 10 }) });
  const spacingWhy = reasonFor('lineSpacing'); if (spacingWhy) spacing.setDisabled(spacingWhy);
  sets.push({ keys: ['lineSpacing'], sync: (v) => spacing.set(v.lineSpacing) });

  const measure = slider('Line length', { min: 30, max: 140, step: 5, value: 70, format: (n) => `${n} characters`, onInput: (n) => commit({ columnWidth: n }) });
  const measureWhy = reasonFor('columnWidth'); if (measureWhy) measure.setDisabled(measureWhy);
  sets.push({ keys: ['columnWidth'], sync: (v) => measure.set(v.columnWidth) });

  const align = segmented<Prefs['textAlign']>('Alignment', 'text-align', [['start', 'Left'], ['justify', 'Justified']], 'start', (a) => commit({ textAlign: a }, true));
  const alignWhy = reasonFor('textAlign'); if (alignWhy) align.setDisabled(alignWhy);
  sets.push({ keys: ['textAlign'], sync: (v) => align.set(v.textAlign) });

  // ---- page
  const layouts: Array<[Prefs['layout'], string]> = ([['continuous', 'Continuous scroll'], ['single', 'One page at a time'], ['spread', 'Two-page spread']] as Array<[Prefs['layout'], string]>)
    .filter(([id]) => !cap?.layouts || cap.layouts.includes(id));
  const layout = h('select', {}, ...layouts.map(([id, text]) => h('option', { value: id, text })));
  const layoutField = field('Page layout', layout);
  const cover = toggle('In a two-page spread, show the cover on its own', true, (on) => commit({ coverAlone: on }, true));
  sets.push({ keys: ['layout'], sync: (v) => { layout.value = v.layout === 'focus' ? 'continuous' : v.layout; cover.setDisabled(v.layout === 'spread' ? null : 'Applies to the two-page spread layout.'); } });
  layout.addEventListener('change', () => commit({ layout: layout.value as Prefs['layout'] }, true));
  sets.push({ keys: ['coverAlone'], sync: (v) => cover.set(v.coverAlone) });
  const pdfMode = h('select', {}, h('option', { value: 'match', text: 'Match the reading mode' }), h('option', { value: 'original', text: 'As printed (original colours)' }));
  pdfMode.addEventListener('change', () => commit({ pdfAppearance: pdfMode.value as Prefs['pdfAppearance'] }, true));
  const pdfField = field('PDF colours', pdfMode, { hint: 'Recolours the page when it is drawn. Scanned images keep their own colours.' });
  sets.push({ keys: ['pdfAppearance'], sync: (v) => { pdfMode.value = v.pdfAppearance; } });

  // ---- display
  const motion = toggle('Reduce motion', false, (on) => commit({ reduceMotion: on }, true), 'Turns off sliding and fading. Applies to every document.');
  sets.push({ keys: ['reduceMotion'], sync: (v) => motion.set(v.reduceMotion) });

  // ---- scope and resets
  const scope = toggle('Apply changes to this document only', app.docOnly && !!session, (on) => { app.docOnly = on; paintScope(); },
    session ? undefined : 'Open a document to keep settings for just that document.');
  if (!session) scope.setDisabled('Open a document to keep settings for just that document.');
  const scopeNote = h('div', { class: 'hint' });
  const resetDoc = button('Reset this document', () => { void run(app.resetDocument()); }, { cls: 'small', disabled: !session });
  const confirmBox = h('div', { class: 'confirm', hidden: true, attrs: { role: 'group', 'aria-label': 'Confirm reset' } });
  const resetAll = button('Reset everything…', () => askConfirm(), { cls: 'small danger' });
  function askConfirm(): void {
    confirmBox.hidden = false; resetAll.hidden = true;
    confirmBox.replaceChildren(h('span', { text: 'Put every reading setting back to its default, for all documents?' }),
      h('div', { class: 'row' }, button('Reset', () => { closeConfirm(); void run(app.resetEverything()); }, { cls: 'small danger' }), button('Cancel', () => closeConfirm(), { cls: 'small' })));
    confirmBox.querySelector<HTMLElement>('button')?.focus();
  }
  function closeConfirm(): void { confirmBox.hidden = true; resetAll.hidden = false; resetAll.focus(); }
  function paintScope(): void {
    const own = session ? Object.keys(session.docPrefs).length : 0;
    const on = app.docOnly && !!session;
    scopeNote.textContent = !session ? 'Changes apply to every document.'
      : on ? `Changes apply to this document only.${own ? ` It has ${own} setting${own === 1 ? '' : 's'} of its own.` : ''}`
        : `Changes apply to every document.${own ? ` This document keeps ${own} of its own, which win over them (use Reset this document).` : ''}`;
    resetDoc.disabled = !session || own === 0;
    resetDoc.title = own === 0 ? 'This document has no settings of its own' : 'Remove this document’s own settings';
  }
  const run = async (op: Promise<void>): Promise<void> => { try { await op; } catch { /* the status area reports it */ } finally { sync(); } };

  // ---- save status
  const dot = h('span', { class: 'save-dot', attrs: { 'aria-hidden': 'true' } });
  const statusText = h('span', { class: 'save-text' });
  const status = h('div', { class: 'save-status', attrs: { 'aria-hidden': 'true' } }, dot, statusText);
  const live = h('div', { class: 'visually-hidden', attrs: { role: 'status', 'aria-live': 'polite' } });
  const alertBox = h('div', { class: 'banner bad ap-error', hidden: true, attrs: { role: 'alert' } });
  let lastPhase = '';
  function paintStatus(): void {
    const phase = batch.phase;
    status.dataset.phase = phase;
    statusText.textContent = phase === 'saving' ? 'Saving…' : phase === 'saved' ? 'Saved' : phase === 'error' ? 'Not saved' : 'Saved as you go';
    if (phase !== lastPhase) {
      if (phase === 'saved') live.textContent = 'Settings saved.';
      else if (phase === 'idle') live.textContent = '';
    }
    lastPhase = phase;
    const err = phase === 'error' ? batch.error : null;
    alertBox.hidden = !err;
    if (err) {
      alertBox.replaceChildren(h('div', { class: 'grow' }, h('strong', { text: 'Not saved. ' }), err, ' The settings shown are the ones that were kept.'),
        button('Try again', () => batch.retry(), { cls: 'small' }), button('Dismiss', () => batch.dismissError(), { cls: 'small flat', title: 'Dismiss' }));
    }
  }

  // ---- keep the controls in step with what the service confirmed
  function refreshWarning(): void {
    const e = state.effective!;
    const r = readability(e.values.fontColor, e.theme, e.values.fontOpacity);
    warn.hidden = r.level === 'ok';
    warn.textContent = r.message ?? '';
    warn.classList.toggle('bad', r.level === 'poor');
  }
  function sync(): void {
    if (!state.effective) return;
    const v = state.effective.values;
    const hold = batch.unconfirmedKeys();                // never overwrite a value the person is still changing with an older answer
    for (const s of sets) if (!s.keys.some((k) => hold.has(k))) s.sync(v);
    refreshWarning(); paintScope();
  }

  const header = h('header', { class: 'ap-head' }, h('h2', { text: 'Appearance' }), h('span', { class: 'spacer' }), status,
    button('', () => pop.close(), { cls: 'icon-btn flat', title: 'Close' }));
  header.querySelector('button')!.append(material('close'));
  header.querySelector('button')!.setAttribute('aria-label', 'Close appearance panel');
  const body = h('div', { class: 'ap-body' },
    alertBox, live,
    h('div', { class: 'group-box scope' }, scope.el, scopeNote),
    section('Reading mode', themeGroup),
    section('Text', colourField.el, opacity.el, warn, size.el, familyField.el, spacing.el, measure.el, align.el),
    section('Page', layoutField.el, cover.el, fixed ? pdfField.el : null),
    section('Display', motion.el),
  );
  const footer = h('footer', { class: 'ap-foot' }, resetDoc, resetAll, confirmBox);
  const el = h('div', { class: 'appearance' }, header, body, footer);

  const offs = [state.on('prefs', sync), state.on('appearance', () => { paintStatus(); if (batch.phase !== 'saving') sync(); })];
  const pop = popover(anchor, el, {
    label: 'Appearance', cls: 'appearance-pop', initialFocus: () => themeInputs.get(state.effective?.values.theme ?? 'system'),
    onClose: () => { for (const off of offs) off(); app.panelOpen = false; void batch.flush(); },
  });
  app.panelOpen = true;
  sync(); paintStatus();
}
