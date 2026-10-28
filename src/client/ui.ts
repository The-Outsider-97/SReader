// Popovers, dialogs and toasts. All keyboard-operable: Escape closes, Tab stays inside a dialog, focus returns to where it was.
import { announce, button, h, nextId } from './dom.js';

const layer = (): HTMLElement => document.getElementById('layer') as HTMLElement;
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function toast(message: string, ms = 4200): void {
  const el = h('div', { class: 'toast', text: message, attrs: { role: 'status' } });
  layer().appendChild(el);
  announce(message);
  window.setTimeout(() => el.remove(), ms);
}

export interface Closable { el: HTMLElement; close(): void }
let current: (Closable & { onClose?: () => void }) | null = null;

export function closePopover(): void { current?.close(); }
export function popoverOpen(): boolean { return current !== null; }

/** A small panel next to a rectangle on screen. Only one is open at a time. */
export function popover(anchor: DOMRect, content: HTMLElement, opts: { label: string; cls?: string; onClose?: () => void; keepFocus?: boolean; initialFocus?: () => HTMLElement | null | undefined } = { label: '' }): Closable {
  closePopover();
  const opener = document.activeElement as HTMLElement | null;
  const el = h('div', { class: `popover ${opts.cls ?? ''}`.trim(), attrs: { role: 'dialog', 'aria-label': opts.label } }, content);
  layer().appendChild(el);
  const place = (): void => {
    // Prefer opening below the anchor and shrink to fit; open above only when there is clearly more room there.
    const below = window.innerHeight - anchor.bottom - 16;
    const above = anchor.top - 16;
    el.style.maxHeight = '';
    const natural = el.offsetHeight;
    const useBelow = natural <= below || below >= above || below >= 360;
    if (natural > (useBelow ? below : above)) el.style.maxHeight = `${Math.max(160, Math.floor(useBelow ? below : above))}px`;
    const w = el.offsetWidth; const hgt = el.offsetHeight;
    let left = anchor.left + anchor.width / 2 - w / 2;
    let top = useBelow ? anchor.bottom + 8 : anchor.top - hgt - 8;
    top = Math.max(8, Math.min(top, window.innerHeight - hgt - 8));
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    el.style.left = `${Math.round(left)}px`; el.style.top = `${Math.round(top)}px`;
  };
  place();
  const onResize = (): void => place();
  window.addEventListener('resize', onResize);
  // A click that dismisses the panel must do nothing else: it must not start a stroke, a selection or a click on the page beneath.
  const swallow = (ev: Event): void => { ev.preventDefault(); ev.stopPropagation(); };
  const onDown = (ev: PointerEvent): void => {
    if (el.contains(ev.target as Node)) return;
    const onPage = !!(ev.target as Element | null)?.closest?.('.view');
    api.close();
    if (!onPage) return;
    swallow(ev);
    const quiet = (e: Event): void => swallow(e);
    for (const type of ['mousedown', 'mouseup', 'click', 'pointerup', 'touchstart']) document.addEventListener(type, quiet, { capture: true, once: true });
    window.setTimeout(() => { for (const type of ['mousedown', 'mouseup', 'click', 'pointerup', 'touchstart']) document.removeEventListener(type, quiet, true); }, 400);
  };
  const onKey = (ev: KeyboardEvent): void => { if (ev.key === 'Escape') { ev.stopPropagation(); api.close(); } };
  window.setTimeout(() => document.addEventListener('pointerdown', onDown, true), 0);
  document.addEventListener('keydown', onKey, true);
  const api: Closable & { onClose?: () => void } = {
    el, onClose: opts.onClose,
    close() {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', onResize);
      el.remove();
      if (current === api) current = null;
      api.onClose?.();
      if (!opts.keepFocus && opener && document.contains(opener)) opener.focus({ preventScroll: true });
    },
  };
  current = api;
  if (opts.initialFocus) (opts.initialFocus() ?? el).focus({ preventScroll: true });
  else if (!opts.keepFocus) (el.querySelector<HTMLElement>(FOCUSABLE) ?? el).focus({ preventScroll: true });
  return api;
}

export interface DialogAction { label: string; primary?: boolean; danger?: boolean; keepOpen?: boolean; onClick?: () => void | boolean | Promise<void | boolean> }

export function dialog(title: string, body: HTMLElement | HTMLElement[], actions: DialogAction[] = [{ label: 'Close', primary: true }], opts: { wide?: boolean; onClose?: () => void } = {}): Closable {
  closePopover();
  const opener = document.activeElement as HTMLElement | null;
  const tid = nextId('dlg');
  const bar = h('div', { class: 'actions' });
  const box = h('div', { class: `dialog${opts.wide ? ' wide' : ''}`, attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': tid } },
    h('h2', { id: tid, text: title }), ...(Array.isArray(body) ? body : [body]), bar);
  const back = h('div', { class: 'backdrop' }, box);
  const stray = (ev: KeyboardEvent): void => {
    if (ev.key === 'Escape') { ev.stopPropagation(); close(); return; }
    if (ev.key !== 'Tab') return;
    const items = Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((x) => x.offsetParent !== null);
    if (!items.length) return;
    const first = items[0]; const last = items[items.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  };
  function close(): void {
    document.removeEventListener('keydown', stray, true);
    back.remove();
    opts.onClose?.();
    if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
  }
  for (const a of actions) {
    bar.appendChild(button(a.label, async () => {
      let keep = a.keepOpen === true;
      if (a.onClick) { const r = await a.onClick(); if (r === false) keep = true; }
      if (!keep) close();
    }, { cls: a.primary ? 'primary' : a.danger ? 'danger' : '' }));
  }
  back.addEventListener('pointerdown', (ev) => { if (ev.target === back) close(); });
  document.addEventListener('keydown', stray, true);
  layer().appendChild(back);
  const first = box.querySelector<HTMLElement>('input, textarea, select') ?? bar.querySelector<HTMLElement>('button.primary, button');
  first?.focus();
  return { el: box, close };
}

export function confirmDialog(title: string, message: string, confirmLabel = 'OK', danger = false): Promise<boolean> {
  return new Promise((resolve) => {
    let answered = false;
    const done = (v: boolean): void => { if (!answered) { answered = true; resolve(v); } };
    dialog(title, h('p', { text: message }), [
      { label: 'Cancel', onClick: () => done(false) },
      { label: confirmLabel, primary: !danger, danger, onClick: () => done(true) },
    ], { onClose: () => done(false) });
  });
}

/** Ask for a password. Resolves with the text, or null if cancelled. The text is held only long enough to send it. */
export function askPassword(title: string, message: string, error?: string): Promise<string | null> {
  return new Promise((resolve) => {
    let answered = false;
    const done = (v: string | null): void => { if (!answered) { answered = true; resolve(v); } };
    const input = h('input', { attrs: { type: 'password', autocomplete: 'off', 'aria-label': 'Password' }, id: nextId('pw') });
    const err = error ? h('p', { class: 'error', text: error, attrs: { role: 'alert' } }) : null;
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); (input.closest('.dialog')?.querySelector('button.primary') as HTMLElement | null)?.click(); } });
    dialog(title, [h('p', { text: message }), err, h('div', { class: 'field' }, h('label', { text: 'Password', attrs: { for: input.id } }), input)].filter(Boolean) as HTMLElement[], [
      { label: 'Cancel', onClick: () => done(null) },
      { label: 'Open', primary: true, onClick: () => done(input.value) },
    ], { onClose: () => done(null) });
  });
}

export function swatches(colors: string[], current: string | null, onPick: (c: string) => void, label = 'Colour'): HTMLElement {
  const row = h('div', { class: 'row', attrs: { role: 'group', 'aria-label': label } });
  for (const c of colors) {
    const b = h('button', { class: 'swatch' + (c === current ? ' current' : ''), title: c, attrs: { type: 'button', 'aria-label': `${label} ${c}`, 'aria-pressed': String(c === current) }, on: { click: () => onPick(c) } });
    b.style.background = c;
    row.appendChild(b);
  }
  return row;
}
