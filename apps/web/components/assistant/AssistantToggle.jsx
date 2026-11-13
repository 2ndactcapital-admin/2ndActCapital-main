"use client";

import { IconMessageCircle } from "@tabler/icons-react";
import { useAssistantPanel } from "@/lib/useAssistantPanel";

/** NAV1 — the ONE control that opens / closes the assistant panel (top bar). Presentation exported for tests. */
export function AssistantToggleButton({ open, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={open}
      aria-controls="assistant-panel"
      data-testid="assistant-toggle"
      title={open ? "Close assistant" : "Open assistant"}
      className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-bg-app transition-opacity hover:opacity-80"
      style={open ? { background: "color-mix(in srgb, var(--2a-gold-light) 14%, transparent)" } : undefined}
    >
      <IconMessageCircle size={18} stroke={1.6} />
      <span>Assistant</span>
    </button>
  );
}

export default function AssistantToggle() {
  const { open, toggle } = useAssistantPanel();
  return <AssistantToggleButton open={open} onToggle={toggle} />;
}
