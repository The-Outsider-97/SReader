export class Emitter {
    map = new Map();
    on(event, fn) {
        let set = this.map.get(event);
        if (!set)
            this.map.set(event, (set = new Set()));
        set.add(fn);
        return () => set.delete(fn);
    }
    emit(event) { for (const fn of [...(this.map.get(event) ?? [])])
        fn(); }
}
export class AppState extends Emitter {
    api;
    prefs;
    defaults;
    effective = null; // effective appearance for the open document (or the global one on the library page)
    doc = null;
    annotations = [];
    history = { canUndo: false, canRedo: false, undoLabel: null, redoLabel: null };
    changedSince = 0;
    tool = 'select';
    highlightColor = '#facc15';
    side = 'none';
    library = [];
    systemDark = false;
    focusMode = false;
    saving = 'idle';
    statusText = '';
    agentsMode = 'auto';
    pendingSearch = ''; // text handed to the search panel (from a selection)
    selectedId = null; // the annotation chosen in the side panel or on the page
    inkSelected = new Set();
    constructor(api) { super(); this.api = api; }
    setTool(t) { if (this.tool !== t) {
        this.tool = t;
        this.emit('tool');
    } }
    setSide(s) { this.side = s; this.emit('side'); }
    setAnnotations(list) {
        this.annotations = list.annotations;
        this.history = list.history;
        this.changedSince = list.changedSinceAnnotated;
        this.emit('annotations');
    }
    upsert(a) {
        const i = this.annotations.findIndex((x) => x.id === a.id);
        if (i >= 0)
            this.annotations[i] = a;
        else
            this.annotations.push(a);
    }
    remove(id) { this.annotations = this.annotations.filter((a) => a.id !== id); }
    get inkTool() { return this.tool === 'pen' || this.tool === 'pencil' || this.tool === 'highlighter' ? this.tool : null; }
}
//# sourceMappingURL=state.js.map