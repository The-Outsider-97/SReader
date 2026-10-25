// Shapes of what the Reader service returns. They mirror slai_reader/api.py; the client never invents data of its own.

export type Layout = 'flow' | 'fixed';
export type Theme = 'light' | 'dark' | 'sepia' | 'contrast' | 'system';
export type ZoomMode = 'custom' | 'fit-width' | 'fit-page';
export type PageLayout = 'continuous' | 'single' | 'spread' | 'focus';
export type AnnotationKind = 'highlight' | 'underline' | 'strike' | 'note' | 'sticky' | 'ink' | 'shape' | 'bookmark';
export type AnchorStatus = 'ok' | 'moved' | 'detached' | 'stale';
export type InkTool = 'pen' | 'pencil' | 'highlighter';

export interface ApiErrorBody { code: string; message: string; details?: Record<string, unknown> }

export interface Prefs {
  theme: Theme; zoomMode: ZoomMode; zoom: number; layout: PageLayout; coverAlone: boolean;
  fontSize: number; fontFamily: 'serif' | 'sans' | 'mono'; lineSpacing: number; columnWidth: number; textAlign: 'start' | 'justify';
  fontColor: string | null; fontOpacity: number; pdfAppearance: 'match' | 'original';
  sidebar: 'none' | 'outline' | 'thumbnails' | 'annotations' | 'search' | 'assistant';
  reduceMotion: boolean; highlightColors: string[]; recentColors: string[];
  tool: Record<InkTool, { color: string; width: number }>;
  assistantEnabled: boolean; assistantMayReadDocument: boolean;
}
export type DocPrefs = Partial<Pick<Prefs, 'theme' | 'zoomMode' | 'zoom' | 'layout' | 'coverAlone' | 'fontSize' | 'fontFamily' | 'lineSpacing' | 'columnWidth' | 'textAlign' | 'fontColor' | 'fontOpacity' | 'pdfAppearance'>>;

export interface Effective {
  values: Prefs; theme: Exclude<Theme, 'system'>; colors: { bg: string; text: string; dark: boolean }; contrast: number; warnings: string[];
}

export type FlowLocation = { type: 'flow'; block: string | null; offset: number; percent: number; n?: number };
export type PageLocation = { type: 'page'; page: number; y: number; percent: number };
export type Location = FlowLocation | PageLocation;

export interface TextSegment { block?: string; page?: number; start: number; end: number; quote?: string; prefix?: string; suffix?: string; status?: AnchorStatus }
export interface Anchor { kind: 'text' | 'pdf-text' | 'block' | 'page'; segments?: TextSegment[]; block?: string; page?: number; x?: number; y?: number; offset?: number }
export interface Annotation {
  id: string; rev: number; doc_id: string; kind: AnnotationKind; anchor: Anchor;
  style: { color: string; opacity: number; width?: number; tool?: InkTool; fill?: string | null };
  body: { title?: string; text?: string; tags?: string[] };
  geometry?: { points?: number[][]; shape?: 'rect' | 'ellipse' | 'line' | 'arrow'; x0?: number; y0?: number; x1?: number; y1?: number };
  group?: string; created: string; modified: string; visible: boolean; fingerprint?: string;
  status: AnchorStatus; text?: string; pos?: [number, number]; resolved: { status: AnchorStatus; segments?: TextSegment[]; block?: string; page?: number };
}
export interface HistoryState { canUndo: boolean; canRedo: boolean; undoLabel: string | null; redoLabel: string | null }
export interface AnnotationList { annotations: Annotation[]; counts: Record<string, number>; history: HistoryState; changedSinceAnnotated: number }

export interface Capabilities {
  layout: Layout; format: string; render: boolean; textSelection: boolean; search: boolean; highlight: boolean; underline: boolean; strike: boolean;
  typedNotes: boolean; stickyNotes: boolean; ink: boolean; bookmarks: boolean; outline: boolean; reflow?: boolean; fontSize?: boolean; fontFamily?: boolean;
  fontColor?: boolean; fontOpacity?: boolean; lineSpacing?: boolean; columnWidth?: boolean; textAlign?: boolean;
  fontColorNote?: string; fontSizeNote?: string; layouts?: PageLayout[]; ocrNeeded?: boolean; copyAllowed?: boolean; annotateAllowed?: boolean;
  nativeAnnotations?: boolean; nativeAnnotationNote?: string; converted?: boolean;
}

export interface DocInfo {
  title: string; format: string; layout: Layout; pages?: number; pageSizes?: number[][]; uniformSize?: boolean; docId: string; fingerprint: string; revision: string | null;
  metadata?: Record<string, string>; restrictions?: { copy: boolean; annotate: boolean; print: boolean; encrypted: boolean }; scan?: { ocrNeeded: boolean };
  warnings: string[]; converted: boolean; backend: string; language?: string; direction?: string; hasCover?: boolean;
  blockCount?: number; comments?: Array<{ id: string; anchorState: string; messages: Array<{ author: string; text: string; created?: string }>; resolved?: boolean }>;
  signatures?: Array<{ id: string; state: string; signer?: string; problems?: string[] }>; references?: Record<string, { id: string; [k: string]: unknown }>; dropped?: string[]; features?: string[];
  totalChars?: number; logicalPages?: number[]; sections?: unknown[]; publication?: Record<string, unknown>;
}

export interface FileStatus { state: 'same' | 'changed' | 'missing' | 'unknown'; message?: string }
export interface CarryOver { fromDocId: string; annotations: number; message: string }
export interface DocPayload {
  sid: string; docId: string; info: DocInfo; capabilities: Capabilities;
  reading: { position: Location | null; progress: number; docPrefs: DocPrefs; effective: Effective };
  file: FileStatus; carryOver: CarryOver | null; warnings: string[]; annotations: AnnotationList; assist: boolean; managedCopy: boolean; path: string | null;
  arrived?: { landing: Location | null; selection: { status: AnchorStatus; segments: TextSegment[]; text: string } | null; notes: string[] };
}

export interface FlowBlock {
  id: string; type: string; text: string; n: number; cum: number; number?: number;
  spans?: Array<{ start: number; end: number; type: string; [k: string]: unknown }>;
  attributes?: Record<string, unknown>; style?: string; section?: string;
  table?: { rows: Array<Array<string | { text?: string; colspan?: number; rowspan?: number }>>; header_rows?: number; caption?: string };
  figure?: { asset: string; alt?: string; caption?: string; decorative?: boolean; url: string };
}
export interface BlocksResponse { start: number; end: number; total: number; blocks: FlowBlock[] }

export interface OutlineItem { title: string; level: number; location: Location }
export interface SearchHit { page?: number; block?: string; start: number | null; end: number | null; inline?: boolean; before: string; match: string; after: string; percent: number }
export interface SearchResult { query: string; hits: SearchHit[]; total: number; truncated: boolean; index?: { indexed: number; total: number; complete: boolean }; ocrNeeded?: boolean }

export interface LibraryRow {
  doc_id: string; title: string | null; format: string | null; path: string | null; progress: number; opened: string | null; annotation_count: number;
  bookmark_count: number; pages: number | null; available: boolean | null; managedCopy: boolean; position?: Location | null;
}

export interface AgentNote { role: string; agent: string | null; status: 'used' | 'skipped' | 'unavailable' | 'failed'; reason: string | null; output: unknown; ms: number }
export interface AssistResult {
  feature: string; status: string; label: string; text?: string; produced_by?: string; note?: string; why?: string; grounded?: number;
  sources?: Array<{ loc: Record<string, number | string>; label: string; text: string }>; sentences?: Array<{ text: string; loc: Record<string, number | string> }>;
  passages?: Array<{ text: string; loc: Record<string, number | string>; label: string }>; notes: AgentNote[]; local?: Record<string, unknown>; query?: string;
}

export interface PdfWordBox { 0: number; 1: number; 2: number; 3: number; 4: number; 5: number; length: 6 }
export interface PdfText { page: number; width: number; height: number; text: string; words: number[][] }
export interface PdfMark { id: string; kind: AnnotationKind; status: AnchorStatus; rects: number[][] }
export interface PdfLink { rect?: number[]; page?: number | null; uri?: string | null; [k: string]: unknown }
export interface PaginateResult { budget: number; total: number; pages: Array<{ n: number; index: number; end: number; block: string; percent: number }> }
