// Keyboard shortcuts as data, so the shortcut list in the app, the handler and the tests all read the same table.

export interface Shortcut { id: string; keys: string; label: string; group: 'Navigation' | 'View' | 'Annotate' | 'Document' }

export const SHORTCUTS: Shortcut[] = [
  { id: 'search', keys: 'Ctrl+F', label: 'Search in this document', group: 'Navigation' },
  { id: 'goto', keys: 'Ctrl+G', label: 'Go to page or position', group: 'Navigation' },
  { id: 'next', keys: 'PageDown', label: 'Next page or screen', group: 'Navigation' },
  { id: 'prev', keys: 'PageUp', label: 'Previous page or screen', group: 'Navigation' },
  { id: 'start', keys: 'Ctrl+Home', label: 'Start of the document', group: 'Navigation' },
  { id: 'end', keys: 'Ctrl+End', label: 'End of the document', group: 'Navigation' },
  { id: 'zoomIn', keys: 'Ctrl+=', label: 'Zoom in', group: 'View' },
  { id: 'zoomOut', keys: 'Ctrl+-', label: 'Zoom out', group: 'View' },
  { id: 'zoomReset', keys: 'Ctrl+0', label: 'Reset zoom to 100%', group: 'View' },
  { id: 'fitWidth', keys: 'Ctrl+1', label: 'Fit width', group: 'View' },
  { id: 'fitPage', keys: 'Ctrl+2', label: 'Fit page', group: 'View' },
  { id: 'focus', keys: 'F9', label: 'Distraction-free reading', group: 'View' },
  { id: 'fullscreen', keys: 'F11', label: 'Full screen', group: 'View' },
  { id: 'sidebar', keys: 'Ctrl+B', label: 'Show or hide the side panel', group: 'View' },
  { id: 'undo', keys: 'Ctrl+Z', label: 'Undo annotation change', group: 'Annotate' },
  { id: 'redo', keys: 'Ctrl+Y', label: 'Redo annotation change', group: 'Annotate' },
  { id: 'highlight', keys: 'Ctrl+Shift+H', label: 'Highlight the selection', group: 'Annotate' },
  { id: 'note', keys: 'Ctrl+Shift+N', label: 'Add a note to the selection', group: 'Annotate' },
  { id: 'bookmark', keys: 'Ctrl+D', label: 'Bookmark this place', group: 'Annotate' },
  { id: 'toolSelect', keys: 'V', label: 'Select text', group: 'Annotate' },
  { id: 'toolPen', keys: 'P', label: 'Pen', group: 'Annotate' },
  { id: 'toolPencil', keys: 'N', label: 'Pencil', group: 'Annotate' },
  { id: 'toolHighlighter', keys: 'H', label: 'Highlighter pen', group: 'Annotate' },
  { id: 'toolEraser', keys: 'E', label: 'Eraser (whole stroke)', group: 'Annotate' },
  { id: 'toolMove', keys: 'M', label: 'Select and move drawings', group: 'Annotate' },
  { id: 'open', keys: 'Ctrl+O', label: 'Open a document', group: 'Document' },
  { id: 'help', keys: '?', label: 'Show shortcuts', group: 'Document' },
  { id: 'escape', keys: 'Escape', label: 'Close a panel, leave a tool', group: 'Document' },
];

export interface KeyLike { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }

export function describe(ev: KeyLike): string {
  const parts: string[] = [];
  if (ev.ctrlKey || ev.metaKey) parts.push('Ctrl');
  if (ev.altKey) parts.push('Alt');
  if (ev.shiftKey && ev.key.length > 1 || (ev.shiftKey && (ev.ctrlKey || ev.metaKey))) parts.push('Shift');
  let key = ev.key;
  if (key === ' ') key = 'Space';
  if (key === '+') key = '=';
  parts.push(key.length === 1 ? key.toUpperCase() : key);
  return parts.join('+');
}

const BY_KEYS = new Map(SHORTCUTS.map((s) => [s.keys, s.id]));
// "Ctrl++" is the usual way to ask for zoom in on keyboards where = needs Shift.
BY_KEYS.set('Ctrl+Shift+=', 'zoomIn');
BY_KEYS.set('Ctrl+Shift+Z', 'redo');

/** The command for a key event, or null. Plain letter shortcuts are ignored while typing in a field. */
export function commandFor(ev: KeyLike, typing: boolean): string | null {
  const keys = describe(ev);
  const id = BY_KEYS.get(keys) ?? null;
  if (id === null) return null;
  const plain = !(ev.ctrlKey || ev.metaKey || ev.altKey) && keys.length <= 2 && keys !== 'Escape' && !keys.startsWith('F');
  if (typing && (plain || keys === 'PageDown' || keys === 'PageUp')) return null;
  return id;
}
