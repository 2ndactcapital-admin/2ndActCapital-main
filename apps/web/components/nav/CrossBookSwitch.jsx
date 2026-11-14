// NAV3 — Cross Book "Clients only / All relationships" (the owner's entities.status). Shown to staff only, and not
// while an owner is chosen (the owner is more specific than the default). Pure: the grid owns the mode.
import { crossBookSwitchShown } from "../../lib/recordTabs.mjs";

export const CROSS_BOOK_MODES = [
  { key: "clients", label: "Clients only" },
  { key: "all", label: "All relationships" },
];

export default function CrossBookSwitch({ me, mode, onMode, ownerSelected = false }) {
  if (!crossBookSwitchShown(me, ownerSelected)) return null;
  const cur = mode === "all" ? "all" : "clients";
  return (
    <div role="radiogroup" aria-label="Relationships shown" data-cross-book={cur}
      className="inline-flex rounded-md border border-border bg-white text-sm">
      {CROSS_BOOK_MODES.map((m) => (
        <button key={m.key} type="button" role="radio" aria-checked={cur === m.key ? "true" : "false"}
          onClick={() => onMode && onMode(m.key)}
          className={`px-3 py-1.5 ${cur === m.key ? "bg-navy text-white" : "text-text-secondary"}`}>
          {m.label}
        </button>
      ))}
    </div>
  );
}
