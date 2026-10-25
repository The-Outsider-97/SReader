// The small amount of state shared by the screens, with change notification. Persistent state lives in the service, not here.
import type { Api } from '../core/api.js';
import type { Annotation, AnnotationList, DocPayload, Effective, HistoryState, InkTool, LibraryRow, Prefs } from '../core/types.js';

export type Tool = 'select' | 'highlight' | 'underline' | 'strike' | 'note' | 'sticky' | 'pen' | 'pencil' | 'highlighter' | 'eraser' | 'move';
export type SideTab = 'none' | 'outline' | 'thumbnails' | 'annotations' | 'search' | 'assistant';

type Listener = () => void;
export class Emitter<E extends string> {
  private map = new Map<E, Set<Listener>>();
  on(event: E, fn: Listener): () => void {
    let set = this.map.get(event);
    if (!set) this.map.set(event, (set = new Set()));
    set.add(fn);
    return () => set!.delete(fn);
  }
  emit(event: E): void { for (const fn of [...(this.map.get(event) ?? [])]) fn(); }
}

export type StateEvent = 'prefs' | 'doc' | 'annotations' | 'tool' | 'side' | 'library' | 'view' | 'status';

export class AppState extends Emitter<StateEvent> {
  api: Api;
  prefs!: Prefs;
  defaults!: Prefs;
  effective: Effective | null = null;           // effective appearance for the open document (or the global one on the library page)
  doc: DocPayload | null = null;
  annotations: Annotation[] = [];
  history: HistoryState = { canUndo: false, canRedo: false, undoLabel: null, redoLabel: null };
  changedSince = 0;
  tool: Tool = 'select';
  highlightColor = '#facc15';
  side: SideTab = 'none';
  library: LibraryRow[] = [];
  systemDark = false;
  focusMode = false;
  saving: 'idle' | 'saving' | 'saved' | 'error' = 'idle';
  statusText = '';
  agentsMode = 'auto';
  pendingSearch = '';                          // text handed to the search panel (from a selection)
  selectedId: string | null = null;            // the annotation chosen in the side panel or on the page
  inkSelected = new Set<string>();

  constructor(api: Api) { super(); this.api = api; }

  setTool(t: Tool): void { if (this.tool !== t) { this.tool = t; this.emit('tool'); } }
  setSide(s: SideTab): void { this.side = s; this.emit('side'); }

  setAnnotations(list: AnnotationList): void {
    this.annotations = list.annotations;
    this.history = list.history;
    this.changedSince = list.changedSinceAnnotated;
    this.emit('annotations');
  }

  upsert(a: Annotation): void {
    const i = this.annotations.findIndex((x) => x.id === a.id);
    if (i >= 0) this.annotations[i] = a; else this.annotations.push(a);
  }
  remove(id: string): void { this.annotations = this.annotations.filter((a) => a.id !== id); }
  get inkTool(): InkTool | null { return this.tool === 'pen' || this.tool === 'pencil' || this.tool === 'highlighter' ? this.tool : null; }
}
