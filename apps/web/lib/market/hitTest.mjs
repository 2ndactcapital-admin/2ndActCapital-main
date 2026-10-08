/**
 * Hover hit-testing for the chart (mkt04b). Pure: no React, no DOM.
 *
 * Same rule as the mockup: take the period nearest the pointer horizontally,
 * then the line whose point at that period is nearest the pointer, and accept
 * it only within HIT_RADIUS pixels. Pointer coordinates arrive in RENDERED
 * pixels; the chart draws in LOGICAL pixels (its viewBox). When the two differ
 * (the SVG is scaled by CSS, or mid-resize) the pointer is divided by the
 * display scale (rendered width / logical width) before any comparison, so
 * the 18-pixel radius is measured in the chart's own units.
 */

export const HIT_RADIUS = 18;

/** Rendered width / logical width; 1 when either is unusable. */
export function displayScale(renderedWidth, logicalWidth) {
  return renderedWidth > 0 && logicalWidth > 0 ? renderedWidth / logicalWidth : 1;
}

/** A pointer event's client position in the chart's logical pixels. */
export function toLogical(clientX, clientY, rect, logicalWidth, logicalHeight) {
  const sx = displayScale(rect.width, logicalWidth);
  const sy = displayScale(rect.height, logicalHeight);
  return { x: (clientX - rect.left) / sx, y: (clientY - rect.top) / sy };
}

/** Index of the value in ascending `xs` nearest to x (-1 when empty). */
export function nearestIndex(xs, x) {
  if (xs.length === 0) return -1;
  let lo = 0;
  let hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid;
    else hi = mid;
  }
  return Math.abs(xs[hi] - x) < Math.abs(xs[lo] - x) ? hi : lo;
}

/**
 * point: {x, y} in logical pixels. periodXs: ascending x of each period.
 * lines: [{key, ys: (number|null)[]}] — each line's y pixel per period.
 * Returns {key, index, x, y, distance} for the nearest line point at the
 * nearest period, or null when none lies within `radius`.
 */
export function hitTest(point, periodXs, lines, radius = HIT_RADIUS) {
  const index = nearestIndex(periodXs, point.x);
  if (index < 0) return null;
  const x = periodXs[index];
  let best = null;
  for (const line of lines) {
    const y = line.ys[index];
    if (y === null || y === undefined || !Number.isFinite(y)) continue;
    const distance = Math.hypot(x - point.x, y - point.y);
    if (distance <= radius && (best === null || distance < best.distance)) {
      best = { key: line.key, index, x, y, distance };
    }
  }
  return best;
}
