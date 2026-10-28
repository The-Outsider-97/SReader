// Batches setting changes made quickly (a slider drag fires dozens) into a few requests, without ever mixing the document's own
// settings with the global ones, and tells the panel which values are still unconfirmed so it never overwrites what the person is
// doing with an older answer from the service. DOM-free: the panel and the tests supply the timers.
/** Settings a single document may override. Must match DOC_FIELDS in slai_reader/prefs.py. */
export const DOC_KEYS = ['theme', 'zoomMode', 'zoom', 'layout', 'coverAlone', 'fontSize', 'fontFamily', 'lineSpacing',
    'columnWidth', 'textAlign', 'fontColor', 'fontOpacity', 'pdfAppearance'];
/** Where a change goes: only a document setting can be kept for one document, and only when the person asked for that and one is open. */
export function scopeFor(key, docOnly, hasDocument) {
    return docOnly && hasDocument && DOC_KEYS.includes(key) ? 'doc' : 'global';
}
const realTimers = { set: (fn, ms) => window.setTimeout(fn, ms), clear: (id) => window.clearTimeout(id) };
export class PatchBatcher {
    opts;
    pending = { doc: {}, global: {} };
    inflight = new Map(); // key -> how many requests carrying it are unanswered
    timer = 0;
    savedTimer = 0;
    active = 0;
    failed = [];
    touchedSinceFailure = new Set();
    epoch = 0;
    phase = 'idle';
    error = null;
    timers;
    constructor(opts) {
        this.opts = opts;
        this.timers = opts.timers ?? realTimers;
    }
    /** Queue a change. `immediate` (a choice, not a drag) sends without waiting. */
    add(scope, patch, immediate = false) {
        for (const key of Object.keys(patch)) {
            this.touchedSinceFailure.add(key);
            // A key can only be pending in one scope: the newest choice wins.
            delete this.pending[scope === 'doc' ? 'global' : 'doc'][key];
        }
        this.pending[scope] = { ...this.pending[scope], ...patch };
        this.set('saving');
        this.timers.clear(this.timer);
        if (immediate)
            void this.flush();
        else
            this.timer = this.timers.set(() => { void this.flush(); }, this.opts.delay);
    }
    hasPending() { return Object.keys(this.pending.doc).length > 0 || Object.keys(this.pending.global).length > 0; }
    /** Keys whose newest value has not been confirmed: still waiting to be sent, or sent and not answered. */
    unconfirmedKeys() {
        return new Set([...Object.keys(this.pending.doc), ...Object.keys(this.pending.global), ...this.inflight.keys()]);
    }
    /** Send what is waiting now (document scope first, then global). Resolves when those requests have been answered. */
    async flush() {
        this.timers.clear(this.timer);
        const jobs = [];
        for (const scope of ['doc', 'global']) {
            const patch = this.pending[scope];
            if (!Object.keys(patch).length)
                continue;
            this.pending[scope] = {};
            jobs.push(this.dispatch(scope, patch));
        }
        await Promise.all(jobs);
    }
    async dispatch(scope, patch) {
        const epoch = this.epoch;
        for (const key of Object.keys(patch))
            this.inflight.set(key, (this.inflight.get(key) ?? 0) + 1);
        this.active++;
        this.set('saving');
        let failure = null;
        try {
            await this.opts.send(scope, patch);
        }
        catch (e) {
            failure = e;
        }
        for (const key of Object.keys(patch)) {
            const n = (this.inflight.get(key) ?? 1) - 1;
            if (n <= 0)
                this.inflight.delete(key);
            else
                this.inflight.set(key, n);
        }
        this.active--;
        if (failure !== null) {
            if (epoch === this.epoch) { // after a reset the old edit is meant to be gone, so it is not offered for retry
                this.failed.push({ scope, patch });
                this.touchedSinceFailure.clear();
                this.error = failure instanceof Error ? failure.message : 'That setting could not be saved.';
            }
            this.set('error');
        }
        else {
            this.settle();
        }
    }
    /** Run an operation that is not a batched change (a reset) so the same Saving / Saved / error status covers it. Rethrows its failure. */
    async track(operation) {
        this.active++;
        this.set('saving');
        try {
            await operation();
        }
        catch (e) {
            this.active--;
            this.error = e instanceof Error ? e.message : 'That could not be saved.';
            this.set('error');
            throw e;
        }
        this.active--;
        this.settle();
    }
    settle() {
        if (this.active === 0 && !this.hasPending()) {
            if (this.phase !== 'error')
                this.set('saved');
            this.timers.clear(this.savedTimer);
            this.savedTimer = this.timers.set(() => { if (this.phase === 'saved')
                this.set('idle'); }, this.opts.savedMs ?? 2200);
        }
    }
    /** Forget changes that have not been sent (a reset replaces them). Requests already sent cannot be taken back. */
    discard(scope) {
        this.timers.clear(this.timer);
        if (scope)
            this.pending[scope] = {};
        else
            this.pending = { doc: {}, global: {} };
        this.failed = scope ? this.failed.filter((f) => f.scope !== scope) : [];
        this.epoch++;
        if (!this.hasPending() && this.active === 0)
            this.set('idle');
    }
    /** Try the failed changes again, except any value the person has changed since. */
    retry() {
        const again = this.failed;
        this.failed = [];
        this.error = null;
        let any = false;
        for (const { scope, patch } of again) {
            const keep = Object.fromEntries(Object.entries(patch).filter(([k]) => !this.touchedSinceFailure.has(k)));
            if (Object.keys(keep).length) {
                this.pending[scope] = { ...keep, ...this.pending[scope] };
                any = true;
            }
        }
        if (any) {
            this.set('saving');
            void this.flush();
        }
        else
            this.set(this.active || this.hasPending() ? 'saving' : 'idle');
    }
    /** Dismiss an error message without retrying. */
    dismissError() {
        this.failed = [];
        this.error = null;
        this.set(this.active || this.hasPending() ? 'saving' : 'idle');
    }
    set(phase) {
        if (phase === 'saving' && this.phase === 'error' && this.failed.length) { /* a new edit after a failure keeps the error visible until it succeeds */
            phase = 'error';
        }
        if (phase !== 'error')
            this.error = phase === 'saving' ? this.error : null;
        if (this.phase === phase) {
            this.opts.onChange?.();
            return;
        }
        this.phase = phase;
        this.opts.onChange?.();
    }
}
//# sourceMappingURL=commit.js.map