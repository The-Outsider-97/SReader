// Drawing helpers: how a pen, pencil or highlighter looks, and how pressure maps to width. The stored stroke is [x, y, pressure] points
// plus a style; how it is drawn is decided here, so every view draws the same stroke the same way.
import type { InkTool } from './types.js';

export interface ToolLook { width: number; opacity: number; cap: 'round' | 'butt'; blend: 'normal' | 'multiply'; pressureGain: number; jitter: boolean }

export function toolLook(tool: InkTool, width: number, opacity: number): ToolLook {
  switch (tool) {
    case 'highlighter': return { width: Math.max(width, 6), opacity: Math.min(opacity, 0.4), cap: 'butt', blend: 'multiply', pressureGain: 0, jitter: false };
    case 'pencil': return { width, opacity: Math.min(opacity, 0.85), cap: 'round', blend: 'normal', pressureGain: 0.6, jitter: true };
    default: return { width, opacity, cap: 'round', blend: 'normal', pressureGain: 0.35, jitter: false };
  }
}

/** Stroke width at a pressure of 0..1 (a mouse reports 0.5, which gives exactly the base width). */
export function widthAt(base: number, pressure: number, gain: number): number {
  const p = Math.min(1, Math.max(0, Number.isFinite(pressure) ? pressure : 0.5));
  return base * (1 + (p - 0.5) * 2 * gain);
}

export interface Stroke { id: string; points: Array<[number, number, number]> }

/** Strokes drawn within ``gapMs`` of each other on the same host belong to one piece of handwriting. */
export function groupId(prev: { id: string; at: number; host: string } | null, host: string, now: number, gapMs = 2500, fresh: () => string): { id: string; at: number; host: string } {
  if (prev && prev.host === host && now - prev.at <= gapMs) return { ...prev, at: now };
  return { id: fresh(), at: now, host };
}
