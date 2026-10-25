// The service counts text in Unicode code points (Python strings); JavaScript counts UTF-16 units. They differ only for characters
// outside the Basic Multilingual Plane (most emoji, some historic scripts, rare CJK). Every offset crosses this boundary through here.

const SURROGATE = /[\ud800-\udfff]/;

export function hasAstral(text: string): boolean { return SURROGATE.test(text); }

/** UTF-16 index -> code point index. */
export function u16ToCp(text: string, index: number): number {
  if (!hasAstral(text)) return index;
  let cp = 0;
  for (let i = 0; i < index && i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length && text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) i++;
    cp++;
  }
  return cp;
}

/** Code point index -> UTF-16 index. */
export function cpToU16(text: string, index: number): number {
  if (!hasAstral(text)) return index;
  let i = 0;
  for (let cp = 0; cp < index && i < text.length; cp++) {
    const c = text.charCodeAt(i);
    const d = i + 1 < text.length ? text.charCodeAt(i + 1) : 0;
    i += c >= 0xd800 && c <= 0xdbff && d >= 0xdc00 && d <= 0xdfff ? 2 : 1;
  }
  return i;
}

export function cpLength(text: string): number { return u16ToCp(text, text.length); }
export function cpSlice(text: string, start: number, end: number): string {
  return hasAstral(text) ? text.slice(cpToU16(text, start), cpToU16(text, end)) : text.slice(start, end);
}

export interface Mark { start: number; end: number }
/** Mark ranges in code points -> UTF-16 ranges for one text. */
export function marksToU16<T extends Mark>(text: string, marks: T[]): T[] {
  if (!hasAstral(text)) return marks;
  return marks.map((m) => ({ ...m, start: cpToU16(text, m.start), end: cpToU16(text, m.end) }));
}
