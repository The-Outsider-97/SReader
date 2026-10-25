// Which pages share a row. Two-page spreads follow a book: the cover stands alone on the right (when ``coverAlone``), then pairs.
export function buildSpreads(pageCount, layout, coverAlone) {
    const rows = [];
    if (layout !== 'spread') {
        for (let i = 0; i < pageCount; i++)
            rows.push([i]);
        return rows;
    }
    let i = 0;
    if (coverAlone && pageCount > 0) {
        rows.push([0]);
        i = 1;
    }
    for (; i < pageCount; i += 2)
        rows.push(i + 1 < pageCount ? [i, i + 1] : [i]);
    return rows;
}
export function spreadIndexOf(spreads, page) {
    const at = spreads.findIndex((s) => s.includes(page));
    return at < 0 ? 0 : at;
}
/** Next/previous spread for page-turn commands in single, spread and focus layouts. */
export function turn(spreads, current, direction) {
    return Math.min(spreads.length - 1, Math.max(0, current + direction));
}
/** In a two-page spread the cover is a single page on the right-hand side; a lone last page sits on the left. */
export function slotsOf(spread, pageCount, coverAlone) {
    if (spread.length === 2)
        return [spread[0], spread[1]];
    const only = spread[0];
    if (coverAlone && only === 0)
        return [null, 0];
    return [only, null];
}
//# sourceMappingURL=spreads.js.map