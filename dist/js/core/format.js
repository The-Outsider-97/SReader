export function formatBytes(n) {
    if (!Number.isFinite(n) || n < 0)
        return '';
    if (n < 1024)
        return `${n} B`;
    const units = ['KB', 'MB', 'GB'];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i++;
    }
    return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
export const percent = (fraction) => `${Math.round(Math.min(1, Math.max(0, fraction || 0)) * 100)}%`;
export function relativeTime(iso, now = Date.now()) {
    if (!iso)
        return '';
    const t = Date.parse(iso);
    if (Number.isNaN(t))
        return '';
    const s = Math.max(0, Math.round((now - t) / 1000));
    if (s < 60)
        return 'just now';
    if (s < 3600)
        return `${Math.floor(s / 60)} min ago`;
    if (s < 86400)
        return `${Math.floor(s / 3600)} h ago`;
    if (s < 86400 * 30)
        return `${Math.floor(s / 86400)} d ago`;
    return new Date(t).toLocaleDateString();
}
export function parseTags(text) {
    const seen = new Map();
    for (const raw of text.split(/[,\n]/)) {
        const t = raw.trim().replace(/^#/, '');
        if (t && t.length <= 40 && !seen.has(t.toLowerCase()))
            seen.set(t.toLowerCase(), t);
    }
    return [...seen.values()].slice(0, 20);
}
export function titleOf(row) {
    if (row.title && row.title.trim())
        return row.title.trim();
    const name = (row.path ?? '').split(/[\\/]/).pop() ?? '';
    return name || 'Untitled';
}
/** "12", "12-14", "p. 12" -> zero-based page number; null if it is not a page. */
export function parsePageNumber(text, pageCount) {
    const m = /^\s*(?:p(?:age)?\.?\s*)?(\d{1,6})\s*$/i.exec(text);
    if (!m)
        return null;
    const n = parseInt(m[1], 10);
    return n >= 1 && n <= pageCount ? n - 1 : null;
}
export function parsePercent(text) {
    const m = /^\s*(\d{1,3}(?:\.\d+)?)\s*%\s*$/.exec(text);
    if (!m)
        return null;
    const v = parseFloat(m[1]);
    return v >= 0 && v <= 100 ? v / 100 : null;
}
//# sourceMappingURL=format.js.map