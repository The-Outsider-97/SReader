// What a document view (text or PDF) offers the rest of the app, and what the app offers the view.
import type { Api } from '../core/api.js';
import type { Pt } from '../core/geometry.js';
import type { Anchor, Annotation, InkTool, Location, SearchHit, TextSegment } from '../core/types.js';
import type { AppState } from './state.js';

export interface SelectionInfo { segments: TextSegment[]; text: string; rect: DOMRect; final: boolean }
export interface PageInfo { current: number; total: number; label: string; approximate: boolean }

export interface ReaderContext {
  readonly state: AppState;
  readonly api: Api;
  readonly sid: string;
  say(message: string): void;
  /** A finished text selection (null when it is cleared). ``final`` is true once the pointer or key is released. */
  selection(info: SelectionInfo | null): void;
  location(loc: Location): void;
  openAnnotation(id: string, near: HTMLElement): void;
  openReference(id: string, near: HTMLElement): void;
  placeSticky(anchor: Anchor, near: DOMRect): void;
  createInk(anchor: Anchor, tool: InkTool, points: Pt[]): Promise<void>;
  eraseAnnotations(ids: string[]): Promise<void>;
  moveInk(items: Array<{ id: string; points: number[][] }>): Promise<void>;
  inkSelection(ids: string[]): void;
  canCopy(): boolean;
}

export interface DocView {
  readonly el: HTMLElement;
  start(initial: Location | null): Promise<void>;
  destroy(): void;
  goTo(loc: Location, flash?: boolean): Promise<void>;
  location(): Location;
  pageInfo(): PageInfo;
  /** Page turn (paged layouts) or a screenful (scrolling layouts). */
  step(direction: 1 | -1): void;
  edge(which: 'start' | 'end'): void;
  annotationsChanged(): void;
  appearanceChanged(): void;
  setHits(hits: SearchHit[], current: number): void;
  setInkSelection(ids: ReadonlySet<string>): void;
  /** Where on screen an annotation is, for placing an editor next to it. */
  rectOf(id: string): DOMRect | null;
  /** The text the current selection covers (for copy / search / summarize). */
  selectedText(): string;
  clearSelection(): void;
  /** The zoom actually in effect, in percent (fit modes included). */
  zoomPercent(): number;
}

export const safeHref = (href: unknown): string | null => {
  if (typeof href !== 'string' || href.length > 2048 || /[\s\\]/.test(href)) return null;
  try {
    const u = new URL(href);
    if (u.protocol === 'http:' || u.protocol === 'https:') return u.username || u.password ? null : u.href;
    if (u.protocol === 'mailto:') return u.href;
  } catch { /* not a URL */ }
  return null;
};
