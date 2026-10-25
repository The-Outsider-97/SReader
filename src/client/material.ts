// Selected outlined vector icons from Google's Material Design icons repository (Apache-2.0).
// https://github.com/google/material-design-icons — embedded for fully offline Reader use.
// These icons affect the interface only, never document typography.
import { button } from './dom.js';

const outlines = {"home":[{"tag":"path","attrs":{"d":"M12 5.69l5 4.5V18h-2v-6H9v6H7v-7.81l5-4.5M12 3L2 12h3v8h6v-6h2v6h6v-8h3L12 3z"}}],"folder_open":[{"tag":"path","attrs":{"d":"M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"}}],"description":[{"tag":"path","attrs":{"d":"M8 16h8v2H8zm0-4h8v2H8zm6-10H6c-1.1 0-2 .9-2 2v16c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm4 18H6V4h7v5h5v11z"}}],"search":[{"tag":"path","attrs":{"d":"M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"}}],"chevron_left":[{"tag":"path","attrs":{"d":"M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12l4.58-4.59z"}}],"chevron_right":[{"tag":"path","attrs":{"d":"M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6-6-6z"}}],"remove":[{"tag":"path","attrs":{"d":"M19 13H5v-2h14v2z"}}],"add":[{"tag":"path","attrs":{"d":"M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"}}],"fit_screen":[{"tag":"path","attrs":{"d":"M6,16h12V8H6V16z M8,10h8v4H8V10z M4,15H2v3c0,1.1,0.9,2,2,2h3v-2H4V15z M4,6h3V4H4C2.9,4,2,4.9,2,6v3h2V6z M20,4h-3v2h3v3 h2V6C22,4.9,21.1,4,20,4z M20,18h-3v2h3c1.1,0,2-0.9,2-2v-3h-2V18z"}}],"text_fields":[{"tag":"path","attrs":{"d":"M2.5 4v3h5v12h3V7h5V4h-13zm19 5h-9v3h3v7h3v-7h3V9z"}}],"menu":[{"tag":"path","attrs":{"d":"M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"}}],"close":[{"tag":"path","attrs":{"d":"M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12 19 6.41z"}}],"arrow_forward_ios":[{"tag":"polygon","attrs":{"points":"6.23,20.23 8,22 18,12 8,2 6.23,3.77 14.46,12"}}],"arrow_back_ios_new":[{"tag":"path","attrs":{"d":"M17.51 3.87L15.73 2.1 5.84 12l9.9 9.9 1.77-1.77L9.38 12l8.13-8.13z"}}],"fullscreen":[{"tag":"path","attrs":{"d":"M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"}}]} as const;
export type MaterialName = keyof typeof outlines;
const namespace = 'http://www.w3.org/2000/svg';
export function material(name: MaterialName): SVGSVGElement {
  const el = document.createElementNS(namespace, 'svg');
  el.setAttribute('viewBox', '0 0 24 24');
  el.setAttribute('class', 'material-icon');
  el.setAttribute('aria-hidden', 'true');
  el.setAttribute('focusable', 'false');
  for (const part of outlines[name]) {
    const shape = document.createElementNS(namespace, part.tag);
    for (const [k, v] of Object.entries(part.attrs)) shape.setAttribute(k, v);
    el.appendChild(shape);
  }
  return el;
}
export function materialButton(name: MaterialName, label: string, onClick: () => void, opts: { cls?: string; keys?: string; disabled?: boolean; pressed?: boolean } = {}): HTMLButtonElement {
  const b = button('', onClick, { cls: 'icon-btn ' + (opts.cls ?? ''), title: label, keys: opts.keys, disabled: opts.disabled, pressed: opts.pressed });
  b.setAttribute('aria-label', label);
  b.appendChild(material(name));
  return b;
}
export function changeMaterial(button: HTMLElement, name: MaterialName): void {
  button.querySelector('svg.material-icon')?.replaceWith(material(name));
}
