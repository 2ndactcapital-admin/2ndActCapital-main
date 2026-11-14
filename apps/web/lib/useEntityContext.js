"use client";

import { useEffect, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { usePermissions } from "@/lib/usePermissions";
import {
  chipState,
  dropRecent,
  effectiveEntityId,
  isEntityId,
  pushRecent,
  readRecent,
  readRemembered,
  reduceLookup,
  resolveSelection,
  urlEntityParam,
  writeRecent,
  writeRemembered,
} from "@/lib/entityContext.mjs";

// NAV2 — the browser side of the one selected-entity context. The rule lives in lib/entityContext.mjs; this file
// only wires it to the URL (useSearchParams), per-browser storage and the chip's lookup route
// (GET /api/entities/{id} → the existing GET /api/v1/entities/{id}, gate unchanged). One store per page load,
// shared by the chip, the sidebar / tab links, the screen banners, Ctrl K and the assistant.

const storage = () => {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

const lookups = new Map(); // id → undefined (in flight) | null (not available) | { id, display_name, entity_type }
let version = 0;
const subs = new Set();
const emit = () => {
  version += 1;
  subs.forEach((fn) => fn());
};
const subscribe = (fn) => {
  subs.add(fn);
  return () => subs.delete(fn);
};
const snapshot = () => version;

let remembered = null;
let rememberedFor = null;
let recent = [];

function loadPerson(userId) {
  if (rememberedFor === userId) return;
  rememberedFor = userId;
  remembered = readRemembered(storage(), userId);
  recent = readRecent(storage(), userId);
  emit();
}

function lookup(id) {
  if (!isEntityId(id) || lookups.has(id)) return;
  lookups.set(id, undefined);
  fetch(`/api/entities/${encodeURIComponent(id)}`, { cache: "no-store" })
    .then(async (res) => reduceLookup(res.ok, await res.json().catch(() => null)))
    .catch(() => null)
    .then((v) => {
      lookups.set(id, v);
      emit();
    });
}

/** Record a chosen / opened entity in Recent and as the remembered selection (per person). */
export function rememberEntity(userId, entity) {
  recent = pushRecent(recent, entity);
  writeRecent(storage(), userId, recent);
  remembered = entity?.id || null;
  writeRemembered(storage(), userId, remembered);
  if (entity && isEntityId(String(entity.id))) lookups.set(String(entity.id), {
    id: String(entity.id), display_name: entity.display_name || "", entity_type: entity.entity_type || null,
  });
  emit();
}

/** Forget the remembered selection (the chip was cleared). */
export function forgetEntity(userId) {
  remembered = null;
  writeRemembered(storage(), userId, null);
  emit();
}

/**
 * { selection, state, entityId (effective — only a visible entity), carryId (what links carry), recent, userId }.
 * `state` is chipState(): none | loading | entity | unavailable.
 */
export function useEntityContext() {
  useSyncExternalStore(subscribe, snapshot, snapshot);
  const sp = useSearchParams();
  const { me } = usePermissions();
  const userId = me?.id || null;

  useEffect(() => {
    if (userId) loadPerson(userId);
  }, [userId]);

  const selection = resolveSelection({
    urlValue: urlEntityParam(sp),
    remembered: rememberedFor === userId ? remembered : null,
  });
  const state = chipState(selection.id, selection.id ? lookups.get(selection.id) : undefined);

  useEffect(() => {
    if (selection.id) lookup(selection.id);
  }, [selection.id]);

  // A resolved entity joins Recent; a not-available one leaves Recent and is no longer remembered.
  useEffect(() => {
    if (!userId) return;
    if (state.kind === "entity" && selection.source === "url" && (recent[0]?.id !== state.id || remembered !== state.id)) {
      rememberEntity(userId, { id: state.id, display_name: state.name, entity_type: state.type });
    } else if (state.kind === "unavailable" && selection.id) {
      const nextRecent = dropRecent(recent, selection.id);
      if (nextRecent.length !== recent.length) {
        recent = nextRecent;
        writeRecent(storage(), userId, recent);
      }
      if (remembered === selection.id) forgetEntity(userId);
    }
  }, [userId, state.kind, state.id, selection.id, selection.source]);

  return {
    selection,
    state,
    entityId: effectiveEntityId(state),
    carryId: state.kind === "entity" || state.kind === "loading" ? state.id : null,
    recent,
    userId,
  };
}
