export function toolLook(tool, width, opacity) {
    switch (tool) {
        case 'highlighter': return { width: Math.max(width, 6), opacity: Math.min(opacity, 0.4), cap: 'butt', blend: 'multiply', pressureGain: 0, jitter: false };
        case 'pencil': return { width, opacity: Math.min(opacity, 0.85), cap: 'round', blend: 'normal', pressureGain: 0.6, jitter: true };
        default: return { width, opacity, cap: 'round', blend: 'normal', pressureGain: 0.35, jitter: false };
    }
}
/** Stroke width at a pressure of 0..1 (a mouse reports 0.5, which gives exactly the base width). */
export function widthAt(base, pressure, gain) {
    const p = Math.min(1, Math.max(0, Number.isFinite(pressure) ? pressure : 0.5));
    return base * (1 + (p - 0.5) * 2 * gain);
}
/** Strokes drawn within ``gapMs`` of each other on the same host belong to one piece of handwriting. */
export function groupId(prev, host, now, gapMs = 2500, fresh) {
    if (prev && prev.host === host && now - prev.at <= gapMs)
        return { ...prev, at: now };
    return { id: fresh(), at: now, host };
}
//# sourceMappingURL=strokes.js.map