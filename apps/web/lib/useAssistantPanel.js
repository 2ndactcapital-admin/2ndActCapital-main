"use client";

import { useEffect, useSyncExternalStore } from "react";
import { usePermissions } from "@/lib/usePermissions";
import { createPanelStore } from "@/lib/assistantPanelPref.mjs";

// NAV1 — one store per page load, shared by the top-bar button and the panel. The rule lives in
// lib/assistantPanelPref.mjs; this file only wires it to the browser and to PATCH /api/users/me.
const store = createPanelStore({
  storage: typeof window !== "undefined" ? (() => { try { return window.localStorage; } catch { return null; } })() : null,
  persist: (posture) => {
    fetch("/api/users/me", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assistant_panel_posture: posture }),
    }).catch(() => {});
  },
});

export function useAssistantPanel() {
  const { me } = usePermissions();
  const open = useSyncExternalStore(store.subscribe, store.get, () => false);
  useEffect(() => { store.start(); }, []);
  useEffect(() => {
    if (me) store.applyAccount(me.assistant_panel_posture ?? null);
  }, [me]);
  return { open, toggle: store.toggle };
}
