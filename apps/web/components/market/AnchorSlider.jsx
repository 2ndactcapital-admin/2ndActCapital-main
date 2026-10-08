import { stepAnchorIndex } from "@/lib/market/chartRequest.mjs";

/**
 * The anchor's native range input (mkt04b Task 4c), bound to the same period
 * index as the draggable bar, so keyboard and screen-reader users move the
 * anchor too: arrows one period, Page Up / Page Down twelve, Home / End to the
 * ends (lib/market/chartRequest.mjs stepAnchorIndex). No hooks: tests call it
 * directly and fire its handlers.
 *
 * Props: periods ([{date}]), index, valueText, onAnchorChange(iso).
 */
export default function AnchorSlider({ periods, index, valueText, onAnchorChange }) {
  return (
    <input
      type="range"
      className="w-full accent-[var(--2a-navy)]"
      min={0}
      max={Math.max(periods.length - 1, 0)}
      step={1}
      value={index}
      aria-label="Anchor date"
      aria-valuetext={valueText}
      onChange={(e) => {
        const i = e.currentTarget.valueAsNumber;
        if (periods[i]) onAnchorChange(periods[i].date);
      }}
      onKeyDown={(e) => {
        const next = stepAnchorIndex(index, e.key, periods.length);
        if (next === null) return;
        e.preventDefault();
        onAnchorChange(periods[next].date);
      }}
      data-chart="anchor-input"
    />
  );
}
