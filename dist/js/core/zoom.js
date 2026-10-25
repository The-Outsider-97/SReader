// Zoom: 25% to 400%, fit-width, fit-page, reset, keyboard steps. 100% means a PDF point is 96/72 CSS pixels (a page prints at its size).
export const ZOOM_MIN = 25;
export const ZOOM_MAX = 400;
export const ZOOM_STEPS = [25, 33, 50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200, 250, 300, 400];
export const CSS_PX_PER_POINT = 96 / 72;
export const clampZoom = (z) => (Number.isFinite(z) ? Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z))) : 100);
export function stepZoom(current, direction) {
    const z = clampZoom(current);
    if (direction > 0)
        return ZOOM_STEPS.find((s) => s > z) ?? ZOOM_MAX;
    for (let i = ZOOM_STEPS.length - 1; i >= 0; i--)
        if (ZOOM_STEPS[i] < z)
            return ZOOM_STEPS[i];
    return ZOOM_MIN;
}
/** CSS pixels per point for the given zoom mode in the given viewport (the area available for pages, in CSS pixels). */
export function pageScale(mode, zoomPercent, page, viewport, columns = 1, gap = 16) {
    const fitW = (viewport.width - gap * (columns + 1)) / (page.width * columns);
    const fitH = (viewport.height - gap * 2) / page.height;
    let scale;
    if (mode === 'fit-width')
        scale = fitW;
    else if (mode === 'fit-page')
        scale = Math.min(fitW, fitH);
    else
        scale = (clampZoom(zoomPercent) / 100) * CSS_PX_PER_POINT;
    // Fit modes still honour the 25%..400% range.
    const lo = (ZOOM_MIN / 100) * CSS_PX_PER_POINT;
    const hi = (ZOOM_MAX / 100) * CSS_PX_PER_POINT;
    return Math.min(hi, Math.max(lo, Number.isFinite(scale) && scale > 0 ? scale : CSS_PX_PER_POINT));
}
export const scaleToPercent = (scale) => clampZoom((scale / CSS_PX_PER_POINT) * 100);
/** The image resolution to request: the on-screen size times the device pixel ratio, capped so a huge zoom never asks for a huge bitmap. */
export function renderScale(cssScale, dpr, maxPixels, page) {
    const wanted = cssScale * Math.max(1, Math.min(dpr, 3));
    const cap = Math.sqrt(maxPixels / (page.width * page.height));
    return Math.max(0.05, Math.min(wanted, cap, 8));
}
//# sourceMappingURL=zoom.js.map