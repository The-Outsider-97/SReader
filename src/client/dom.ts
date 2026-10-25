// A tiny DOM builder. Text always goes in through textContent / text nodes - never innerHTML - so nothing that comes from a
// document (titles, text, metadata, tags, file names) can inject markup.

export type Child = Node | string | number | null | undefined | false;
export interface Props {
  class?: string; id?: string; text?: string; title?: string;
  attrs?: Record<string, string | number | boolean | undefined>;
  data?: Record<string, string | number | undefined>;
  style?: Record<string, string | number | undefined>;
  on?: { [K in keyof HTMLElementEventMap]?: (ev: HTMLElementEventMap[K]) => void };
  value?: string; checked?: boolean; disabled?: boolean; hidden?: boolean;
}

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, props: Props = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  apply(el, props);
  append(el, children);
  return el;
}

export function apply(el: HTMLElement | SVGElement, props: Props): void {
  if (props.class) el.setAttribute('class', props.class);
  if (props.id) el.id = props.id;
  if (props.title) el.setAttribute('title', props.title);
  if (props.text !== undefined) el.textContent = props.text;
  if (props.attrs) for (const [k, v] of Object.entries(props.attrs)) { if (v !== undefined && v !== false) el.setAttribute(k, v === true ? '' : String(v)); }
  if (props.data && el instanceof HTMLElement) for (const [k, v] of Object.entries(props.data)) if (v !== undefined) el.dataset[k] = String(v);
  if (props.style) for (const [k, v] of Object.entries(props.style)) { if (v !== undefined) el.style.setProperty(k.startsWith('--') ? k : k.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase()), String(v)); }
  if (props.on) for (const [k, fn] of Object.entries(props.on)) el.addEventListener(k, fn as EventListener);
  if (props.value !== undefined && 'value' in el) (el as unknown as HTMLInputElement).value = props.value;
  if (props.checked !== undefined && 'checked' in el) (el as unknown as HTMLInputElement).checked = props.checked;
  if (props.disabled !== undefined && 'disabled' in el) (el as unknown as HTMLButtonElement).disabled = props.disabled;
  if (props.hidden && el instanceof HTMLElement) el.hidden = true;
}

export function append(parent: Node, children: Child[]): void {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    parent.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
}

export function clear(el: Node): void { while (el.firstChild) el.removeChild(el.firstChild); }
export function replace(el: Node, ...children: Child[]): void { clear(el); append(el, children); }

const SVG = 'http://www.w3.org/2000/svg';
export function svg<K extends keyof SVGElementTagNameMap>(tag: K, props: Props = {}, ...children: Child[]): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG, tag);
  apply(el, props);
  append(el, children);
  return el;
}

/** A button with a visible text label, or an icon (a single symbol) plus an accessible name. */
export function button(label: string, onClick: () => void, opts: { icon?: string; cls?: string; title?: string; pressed?: boolean; disabled?: boolean; id?: string; keys?: string } = {}): HTMLButtonElement {
  const b = h('button', {
    class: `btn ${opts.cls ?? ''}`.trim(), id: opts.id, title: opts.title ? (opts.keys ? `${opts.title} (${opts.keys})` : opts.title) : opts.keys ? `${label} (${opts.keys})` : undefined,
    attrs: { type: 'button', 'aria-label': opts.icon ? (opts.title ?? label) : undefined, 'aria-pressed': opts.pressed === undefined ? undefined : String(opts.pressed), 'aria-keyshortcuts': opts.keys?.replace(/\s/g, '') },
    disabled: opts.disabled, on: { click: () => onClick() },
  });
  if (opts.icon) append(b, [h('span', { class: 'ico', text: opts.icon, attrs: { 'aria-hidden': 'true' } }), label ? h('span', { class: 'lbl', text: label }) : null]);
  else b.textContent = label;
  return b;
}

export function setPressed(b: HTMLElement, on: boolean): void {
  b.setAttribute('aria-pressed', String(on));
  b.classList.toggle('active', on);
}

let uid = 0;
export const nextId = (prefix: string): string => `${prefix}-${++uid}`;

export function labelled(label: string, control: HTMLElement, hint?: string): HTMLElement {
  const id = control.id || (control.id = nextId('f'));
  const wrap = h('div', { class: 'field' }, h('label', { text: label, attrs: { for: id } }), control);
  if (hint) { const hid = nextId('hint'); control.setAttribute('aria-describedby', hid); wrap.appendChild(h('div', { class: 'hint', id: hid, text: hint })); }
  return wrap;
}

export function announce(message: string, urgent = false): void {
  const region = document.getElementById(urgent ? 'alerts' : 'live');
  if (!region) return;
  region.textContent = '';
  window.setTimeout(() => { region.textContent = message; }, 30);
}
