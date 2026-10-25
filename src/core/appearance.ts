// Colour pairs for the four reading modes and the readability checks. Dark, sepia and high contrast are designed palettes (text and
// paper chosen together), not an inverted picture. Text colour and opacity are applied on top of the mode's palette.

export interface Palette { bg: string; surface: string; text: string; muted: string; link: string; border: string; mark: string; dark: boolean }

export const PALETTES: Record<'light' | 'dark' | 'sepia' | 'contrast', Palette> = {
  light: { bg: '#ffffff', surface: '#f7f7f8', text: '#1a1a1a', muted: '#555b66', link: '#1d4ed8', border: '#d4d4d8', mark: '#facc15', dark: false },
  sepia: { bg: '#f4ecd8', surface: '#ebe1c7', text: '#433422', muted: '#6b5a43', link: '#8a4b08', border: '#cdbf9f', mark: '#e0a32a', dark: false },
  dark: { bg: '#161b22', surface: '#1e252e', text: '#dde1e7', muted: '#a0a8b4', link: '#7cb7ff', border: '#323b47', mark: '#d6a72d', dark: true },
  contrast: { bg: '#000000', surface: '#0b0b0b', text: '#ffffff', muted: '#f0f0f0', link: '#ffff00', border: '#ffffff', mark: '#ffd400', dark: true },
};

export function resolveTheme(theme: string, systemDark: boolean): 'light' | 'dark' | 'sepia' | 'contrast' {
  if (theme === 'system') return systemDark ? 'dark' : 'light';
  return theme === 'dark' || theme === 'sepia' || theme === 'contrast' ? theme : 'light';
}

export function parseHex(value: string | null | undefined): [number, number, number] | null {
  if (typeof value !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(value)) return null;
  return [parseInt(value.slice(1, 3), 16), parseInt(value.slice(3, 5), 16), parseInt(value.slice(5, 7), 16)];
}

const lin = (c: number): number => { const x = c / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
export function luminance(rgb: [number, number, number]): number { return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]); }
export function contrastRatio(a: [number, number, number], b: [number, number, number]): number {
  const la = luminance(a); const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Text drawn at ``opacity`` (30..100 %) over ``bg``: the colour the eye actually gets. */
export function blend(fg: [number, number, number], bg: [number, number, number], opacity: number): [number, number, number] {
  const a = Math.min(1, Math.max(0, opacity));
  return [0, 1, 2].map((i) => Math.round(bg[i] + (fg[i] - bg[i]) * a)) as [number, number, number];
}

export interface Readability { ratio: number; level: 'ok' | 'low' | 'poor'; message: string | null }
export function readability(textHex: string | null, theme: 'light' | 'dark' | 'sepia' | 'contrast', opacityPercent: number): Readability {
  const p = PALETTES[theme];
  const fg = parseHex(textHex ?? p.text) ?? (parseHex(p.text) as [number, number, number]);
  const bg = parseHex(p.bg) as [number, number, number];
  const ratio = contrastRatio(blend(fg, bg, opacityPercent / 100), bg);
  if (ratio >= 4.5) return { ratio, level: 'ok', message: null };
  if (ratio >= 3) return { ratio, level: 'low', message: `Contrast ${ratio.toFixed(1)}:1 is below the recommended 4.5:1 for body text.` };
  return { ratio, level: 'poor', message: `Contrast ${ratio.toFixed(1)}:1 makes this hard to read.` };
}

/** Highlight colour on a given paper: the stored colour is kept, the translucency is what changes with the mode so text stays readable. */
export function markStyle(color: string, opacity: number, theme: 'light' | 'dark' | 'sepia' | 'contrast'): { background: string; text?: string } {
  const rgb = parseHex(color) ?? [250, 204, 21];
  const a = theme === 'contrast' ? Math.max(opacity, 0.7) : theme === 'dark' ? Math.min(opacity, 0.5) : opacity;
  return { background: `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${a})`, text: theme === 'contrast' ? '#000000' : undefined };
}
