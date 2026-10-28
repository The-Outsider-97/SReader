// The small set of form components every panel uses, so a slider, a switch or a segmented choice looks and behaves the same everywhere.
// Each builder returns the element plus what a panel needs to keep it in step with the confirmed settings.
import { h, nextId } from './dom.js';
/** A labelled control with an optional hint. A disabled control always shows why, and the reason is tied to it for screen readers. */
export function field(label, control, opts = {}) {
    const id = control.id || (control.id = nextId('c'));
    const hintId = nextId('hint');
    const hint = h('div', { class: 'hint', id: hintId, text: opts.hint ?? '', hidden: !opts.hint });
    const labelEl = h('label', { text: label, attrs: { for: id } });
    if (opts.labelExtra)
        labelEl.append(' ', opts.labelExtra);
    const el = h('div', { class: `field${opts.inline ? ' inline' : ''}` }, labelEl, control, hint);
    const setDisabled = (reason) => {
        control.disabled = reason !== null;
        el.classList.toggle('is-disabled', reason !== null);
        hint.textContent = reason ?? opts.hint ?? '';
        hint.hidden = !(reason ?? opts.hint);
        if (reason !== null)
            control.setAttribute('aria-describedby', hintId);
        else if (!opts.hint)
            control.removeAttribute('aria-describedby');
    };
    return { el, setDisabled, control };
}
export function slider(label, o) {
    const input = h('input', { attrs: { type: 'range', min: o.min, max: o.max, step: o.step }, value: String(o.value) });
    const out = h('span', { class: 'num value', text: o.format(o.value), attrs: { 'aria-hidden': 'true' } });
    const paint = (n) => { out.textContent = o.format(n); input.setAttribute('aria-valuetext', o.format(n)); };
    paint(o.value);
    input.addEventListener('input', () => { const n = Number(input.value); paint(n); o.onInput(n); });
    const f = field(label, input, { hint: o.hint, labelExtra: out });
    return { ...f, input, set: (n) => { input.value = String(n); paint(n); } };
}
/** A row of mutually exclusive options (radio buttons drawn as a segmented control). */
export function segmented(label, name, options, value, onChange) {
    const labelId = nextId('lbl');
    const hint = h('div', { class: 'hint', id: nextId('hint'), hidden: true });
    const group = h('div', { class: 'seg', attrs: { role: 'radiogroup', 'aria-labelledby': labelId } });
    const inputs = new Map();
    for (const [id, text] of options) {
        const rid = nextId('seg');
        const input = h('input', { class: 'visually-hidden', attrs: { type: 'radio', name, id: rid }, checked: id === value });
        input.addEventListener('change', () => { if (input.checked)
            onChange(id); });
        inputs.set(id, input);
        group.append(input, h('label', { text, attrs: { for: rid } }));
    }
    const el = h('div', { class: 'field' }, h('div', { class: 'label', id: labelId, text: label }), group, hint);
    return {
        el, set: (v) => { const i = inputs.get(v); if (i)
            i.checked = true; },
        setDisabled: (reason) => {
            for (const i of inputs.values()) {
                i.disabled = reason !== null;
                if (reason !== null)
                    i.setAttribute('aria-describedby', hint.id);
                else
                    i.removeAttribute('aria-describedby');
            }
            el.classList.toggle('is-disabled', reason !== null);
            hint.textContent = reason ?? '';
            hint.hidden = reason === null;
        },
    };
}
export function toggle(label, checked, onChange, hint) {
    const input = h('input', { attrs: { type: 'checkbox', role: 'switch' }, checked });
    input.id = nextId('sw');
    const hintEl = h('div', { class: 'hint', id: nextId('hint'), text: hint ?? '', hidden: !hint });
    input.addEventListener('change', () => onChange(input.checked));
    const el = h('div', { class: 'field switchrow' }, h('label', { class: 'switch', attrs: { for: input.id } }, input, h('span', { text: label })), hintEl);
    return {
        el, input, set: (on) => { input.checked = on; },
        setDisabled: (reason) => {
            input.disabled = reason !== null;
            el.classList.toggle('is-disabled', reason !== null);
            hintEl.textContent = reason ?? hint ?? '';
            hintEl.hidden = !(reason ?? hint);
            if (reason !== null)
                input.setAttribute('aria-describedby', hintEl.id);
            else
                input.removeAttribute('aria-describedby');
        },
    };
}
/** A titled group inside a panel. */
export function section(title, ...children) {
    const id = nextId('sec');
    return h('section', { class: 'group-box', attrs: { 'aria-labelledby': id } }, h('h3', { id, text: title }), ...children.filter(Boolean));
}
//# sourceMappingURL=controls.js.map