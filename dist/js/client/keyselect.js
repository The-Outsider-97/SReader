// Selecting text with the keyboard alone.
//
// Browsers only extend a selection with Shift+Arrow inside editable text or in "caret browsing" mode (F7), which most people never
// turn on. Without this, someone who cannot use a mouse could read a document but never mark it. So: Shift+Arrow starts a selection at
// the top of what is on screen (or continues the current one), Ctrl+Shift+Arrow moves by word, and Shift+Up/Down by line.
// The result is an ordinary browser selection, so the highlight, note and copy commands work on it unchanged.
const DIRECTION = { ArrowRight: 'forward', ArrowDown: 'forward', ArrowLeft: 'backward', ArrowUp: 'backward' };
export function bindKeyboardSelection(root) {
    const onKey = (e) => {
        const direction = DIRECTION[e.key];
        if (!direction || !e.shiftKey || e.altKey || e.metaKey)
            return;
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable))
            return;
        const sel = window.getSelection();
        if (!sel || typeof sel.modify !== 'function')
            return;
        if (sel.rangeCount === 0 || !root.contains(sel.anchorNode)) {
            const box = root.getBoundingClientRect();
            const probe = (x, y) => {
                const d = document;
                if (d.caretRangeFromPoint)
                    return d.caretRangeFromPoint(x, y);
                const p = d.caretPositionFromPoint?.(x, y);
                if (!p)
                    return null;
                const r = document.createRange();
                r.setStart(p.offsetNode, p.offset);
                r.collapse(true);
                return r;
            };
            let start = null;
            for (let y = box.top + 24; y < box.bottom - 8 && !start; y += 18) { // the first line of text that is actually on screen
                for (const x of [box.left + box.width * 0.25, box.left + box.width * 0.5, box.left + 40]) {
                    const r = probe(x, y);
                    if (r && root.contains(r.startContainer) && r.startContainer.nodeType === Node.TEXT_NODE && (r.startContainer.textContent ?? '').trim()) {
                        start = r;
                        break;
                    }
                }
            }
            if (!start)
                return;
            sel.removeAllRanges();
            sel.addRange(start);
        }
        const granularity = e.key === 'ArrowUp' || e.key === 'ArrowDown' ? 'line' : e.ctrlKey ? 'word' : 'character';
        sel.modify('extend', direction, granularity);
        e.preventDefault();
        e.stopPropagation();
    };
    root.addEventListener('keydown', onKey);
    return () => root.removeEventListener('keydown', onKey);
}
//# sourceMappingURL=keyselect.js.map