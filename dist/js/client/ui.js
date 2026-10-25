// Popovers, dialogs and toasts. All keyboard-operable: Escape closes, Tab stays inside a dialog, focus returns to where it was.
import { announce, button, h, nextId } from './dom.js';
const layer = () => document.getElementById('layer');
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
export function toast(message, ms = 4200) {
    const el = h('div', { class: 'toast', text: message, attrs: { role: 'status' } });
    layer().appendChild(el);
    announce(message);
    window.setTimeout(() => el.remove(), ms);
}
let current = null;
export function closePopover() { current?.close(); }
export function popoverOpen() { return current !== null; }
/** A small panel next to a rectangle on screen. Only one is open at a time. */
export function popover(anchor, content, opts = { label: '' }) {
    closePopover();
    const opener = document.activeElement;
    const el = h('div', { class: `popover ${opts.cls ?? ''}`.trim(), attrs: { role: 'dialog', 'aria-label': opts.label } }, content);
    layer().appendChild(el);
    const place = () => {
        const w = el.offsetWidth;
        const hgt = el.offsetHeight;
        let left = anchor.left + anchor.width / 2 - w / 2;
        let top = anchor.bottom + 8;
        if (top + hgt > window.innerHeight - 8)
            top = Math.max(8, anchor.top - hgt - 8);
        left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
        el.style.left = `${Math.round(left)}px`;
        el.style.top = `${Math.round(top)}px`;
    };
    place();
    const onDown = (ev) => { if (!el.contains(ev.target))
        api.close(); };
    const onKey = (ev) => { if (ev.key === 'Escape') {
        ev.stopPropagation();
        api.close();
    } };
    window.setTimeout(() => document.addEventListener('pointerdown', onDown, true), 0);
    document.addEventListener('keydown', onKey, true);
    const api = {
        el, onClose: opts.onClose,
        close() {
            document.removeEventListener('pointerdown', onDown, true);
            document.removeEventListener('keydown', onKey, true);
            el.remove();
            if (current === api)
                current = null;
            api.onClose?.();
            if (!opts.keepFocus && opener && document.contains(opener))
                opener.focus({ preventScroll: true });
        },
    };
    current = api;
    if (!opts.keepFocus)
        (el.querySelector(FOCUSABLE) ?? el).focus({ preventScroll: true });
    return api;
}
export function dialog(title, body, actions = [{ label: 'Close', primary: true }], opts = {}) {
    closePopover();
    const opener = document.activeElement;
    const tid = nextId('dlg');
    const bar = h('div', { class: 'actions' });
    const box = h('div', { class: `dialog${opts.wide ? ' wide' : ''}`, attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': tid } }, h('h2', { id: tid, text: title }), ...(Array.isArray(body) ? body : [body]), bar);
    const back = h('div', { class: 'backdrop' }, box);
    const stray = (ev) => {
        if (ev.key === 'Escape') {
            ev.stopPropagation();
            close();
            return;
        }
        if (ev.key !== 'Tab')
            return;
        const items = Array.from(box.querySelectorAll(FOCUSABLE)).filter((x) => x.offsetParent !== null);
        if (!items.length)
            return;
        const first = items[0];
        const last = items[items.length - 1];
        if (ev.shiftKey && document.activeElement === first) {
            ev.preventDefault();
            last.focus();
        }
        else if (!ev.shiftKey && document.activeElement === last) {
            ev.preventDefault();
            first.focus();
        }
    };
    function close() {
        document.removeEventListener('keydown', stray, true);
        back.remove();
        opts.onClose?.();
        if (opener && document.contains(opener))
            opener.focus({ preventScroll: true });
    }
    for (const a of actions) {
        bar.appendChild(button(a.label, async () => {
            let keep = a.keepOpen === true;
            if (a.onClick) {
                const r = await a.onClick();
                if (r === false)
                    keep = true;
            }
            if (!keep)
                close();
        }, { cls: a.primary ? 'primary' : a.danger ? 'danger' : '' }));
    }
    back.addEventListener('pointerdown', (ev) => { if (ev.target === back)
        close(); });
    document.addEventListener('keydown', stray, true);
    layer().appendChild(back);
    const first = box.querySelector('input, textarea, select') ?? bar.querySelector('button.primary, button');
    first?.focus();
    return { el: box, close };
}
export function confirmDialog(title, message, confirmLabel = 'OK', danger = false) {
    return new Promise((resolve) => {
        let answered = false;
        const done = (v) => { if (!answered) {
            answered = true;
            resolve(v);
        } };
        dialog(title, h('p', { text: message }), [
            { label: 'Cancel', onClick: () => done(false) },
            { label: confirmLabel, primary: !danger, danger, onClick: () => done(true) },
        ], { onClose: () => done(false) });
    });
}
/** Ask for a password. Resolves with the text, or null if cancelled. The text is held only long enough to send it. */
export function askPassword(title, message, error) {
    return new Promise((resolve) => {
        let answered = false;
        const done = (v) => { if (!answered) {
            answered = true;
            resolve(v);
        } };
        const input = h('input', { attrs: { type: 'password', autocomplete: 'off', 'aria-label': 'Password' }, id: nextId('pw') });
        const err = error ? h('p', { class: 'error', text: error, attrs: { role: 'alert' } }) : null;
        input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') {
            ev.preventDefault();
            input.closest('.dialog')?.querySelector('button.primary')?.click();
        } });
        dialog(title, [h('p', { text: message }), err, h('div', { class: 'field' }, h('label', { text: 'Password', attrs: { for: input.id } }), input)].filter(Boolean), [
            { label: 'Cancel', onClick: () => done(null) },
            { label: 'Open', primary: true, onClick: () => done(input.value) },
        ], { onClose: () => done(null) });
    });
}
export function swatches(colors, current, onPick, label = 'Colour') {
    const row = h('div', { class: 'row', attrs: { role: 'group', 'aria-label': label } });
    for (const c of colors) {
        const b = h('button', { class: 'swatch' + (c === current ? ' current' : ''), title: c, attrs: { type: 'button', 'aria-label': `${label} ${c}`, 'aria-pressed': String(c === current) }, on: { click: () => onPick(c) } });
        b.style.background = c;
        row.appendChild(b);
    }
    return row;
}
//# sourceMappingURL=ui.js.map