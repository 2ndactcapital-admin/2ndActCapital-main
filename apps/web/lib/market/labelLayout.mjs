/**
 * End-of-line label placement (mkt04b). Pure: no React, no DOM.
 *
 * Each label wants to sit at its line's last y. Labels are kept in that order
 * (top to bottom), at least LABEL_GAP pixels apart, and inside [top, bottom].
 * A downward pass pushes overlaps apart, an upward pass pulls the stack back
 * inside the bottom edge; if they cannot all fit, they are spread evenly over
 * the whole height (the gap shrinks, the order never changes).
 */

export const LABEL_GAP = 14;

/** items: [{key, y}] -> [{key, y, desiredY}] sorted top to bottom. */
export function layoutLabels(items, { top, bottom, gap = LABEL_GAP }) {
  const sorted = items
    .map((it, i) => ({ ...it, desiredY: it.y, order: i }))
    .sort((a, b) => a.desiredY - b.desiredY || a.order - b.order);
  const n = sorted.length;
  if (n === 0) return [];
  if ((n - 1) * gap > bottom - top) {
    const step = n > 1 ? (bottom - top) / (n - 1) : 0;
    return sorted.map(({ order, ...it }, i) => ({ ...it, y: top + i * step }));
  }
  const ys = sorted.map((it) => Math.min(Math.max(it.desiredY, top), bottom));
  for (let i = 1; i < n; i += 1) ys[i] = Math.max(ys[i], ys[i - 1] + gap);
  if (ys[n - 1] > bottom) {
    ys[n - 1] = bottom;
    for (let i = n - 2; i >= 0; i -= 1) ys[i] = Math.min(ys[i], ys[i + 1] - gap);
  }
  return sorted.map(({ order, ...it }, i) => ({ ...it, y: ys[i] }));
}
