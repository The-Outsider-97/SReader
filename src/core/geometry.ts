// Pure geometry for pages and drawings. Positions are fractions of the host (the displayed page, or a block), never screen pixels,
// so they survive zoom, resize, rotation and a different window.

export interface Pt { x: number; y: number; p?: number }
export type Rect = [number, number, number, number];            // left, top, right, bottom

export const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));
export const clamp01 = (v: number): number => clamp(v, 0, 1);

/** A pointer position inside a box, as fractions of that box. ``allowOutside`` keeps values beyond 0..1 (for drawings that leave a block). */
export function toFraction(clientX: number, clientY: number, box: { left: number; top: number; width: number; height: number }, allowOutside = false): Pt {
  const w = box.width || 1;
  const h = box.height || 1;
  const x = (clientX - box.left) / w;
  const y = (clientY - box.top) / h;
  return allowOutside ? { x: clamp(x, -0.5, 1.5), y: clamp(y, -1, 3) } : { x: clamp01(x), y: clamp01(y) };
}

export function distanceToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  const t = clamp(((p.x - a.x) * dx + (p.y - a.y) * dy) / len2, 0, 1);
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/** Ramer-Douglas-Peucker: fewer points for the same line, so a stroke is hundreds of numbers, not thousands. */
export function simplify(points: Pt[], epsilon = 0.0012): Pt[] {
  if (points.length < 3) return points.slice();
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack: Array<[number, number]> = [[0, points.length - 1]];
  while (stack.length) {
    const [lo, hi] = stack.pop() as [number, number];
    let worst = 0;
    let at = -1;
    for (let i = lo + 1; i < hi; i++) {
      const d = distanceToSegment(points[i], points[lo], points[hi]);
      if (d > worst) { worst = d; at = i; }
    }
    if (at >= 0 && worst > epsilon) {
      keep[at] = 1;
      stack.push([lo, at], [at, hi]);
    }
  }
  return points.filter((_, i) => keep[i] === 1);
}

export function bounds(points: Pt[]): Rect {
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const p of points) { l = Math.min(l, p.x); t = Math.min(t, p.y); r = Math.max(r, p.x); b = Math.max(b, p.y); }
  return [l, t, r, b];
}

export function rectIntersects(a: Rect, b: Rect): boolean {
  return a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];
}

export function rectFromPoints(a: Pt, b: Pt): Rect {
  return [Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)];
}

export function inflate(r: Rect, by: number): Rect {
  return [r[0] - by, r[1] - by, r[2] + by, r[3] + by];
}

/** True if the point is within ``tol`` (in the same units as the points) of the polyline. */
export function hitsPolyline(p: Pt, line: Pt[], tol: number): boolean {
  if (line.length === 1) return Math.hypot(p.x - line[0].x, p.y - line[0].y) <= tol;
  for (let i = 1; i < line.length; i++) if (distanceToSegment(p, line[i - 1], line[i]) <= tol) return true;
  return false;
}

/** Whole-stroke eraser: does the eraser's path, with its radius, touch the stroke? Distances use pixels so the eraser feels round. */
export function eraserTouches(path: Pt[], stroke: Pt[], radiusPx: number, box: { width: number; height: number }): boolean {
  const px = (p: Pt): Pt => ({ x: p.x * box.width, y: p.y * box.height });
  const s = stroke.map(px);
  for (const e of path) {
    if (hitsPolyline(px(e), s, radiusPx)) return true;
  }
  return false;
}

export function translate(points: Pt[], dx: number, dy: number): Pt[] {
  return points.map((p) => ({ ...p, x: p.x + dx, y: p.y + dy }));
}

/** Keep a moved drawing inside the allowed frame (fractions) by shifting the move, not by distorting the drawing. */
export function limitMove(r: Rect, dx: number, dy: number, frame: Rect): { dx: number; dy: number } {
  const lx = frame[0] - r[0];
  const hx = frame[2] - r[2];
  const ly = frame[1] - r[1];
  const hy = frame[3] - r[3];
  return { dx: lx <= hx ? clamp(dx, lx, hx) : 0, dy: ly <= hy ? clamp(dy, ly, hy) : 0 };
}

/** A smooth SVG path through the points (quadratic curves between midpoints), in pixels. */
export function smoothPath(points: Array<{ x: number; y: number }>): string {
  if (points.length === 0) return '';
  const f = (n: number): string => (Math.round(n * 100) / 100).toString();
  if (points.length === 1) return `M${f(points[0].x)} ${f(points[0].y)}l0.01 0`;
  if (points.length === 2) return `M${f(points[0].x)} ${f(points[0].y)}L${f(points[1].x)} ${f(points[1].y)}`;
  let d = `M${f(points[0].x)} ${f(points[0].y)}`;
  for (let i = 1; i < points.length - 1; i++) {
    const mx = (points[i].x + points[i + 1].x) / 2;
    const my = (points[i].y + points[i + 1].y) / 2;
    d += `Q${f(points[i].x)} ${f(points[i].y)} ${f(mx)} ${f(my)}`;
  }
  const last = points[points.length - 1];
  return d + `L${f(last.x)} ${f(last.y)}`;
}
