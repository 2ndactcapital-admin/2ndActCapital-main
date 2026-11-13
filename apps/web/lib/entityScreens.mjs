/**
 * NAV2 — which book screens filter by the selected entity, and which say they do not. ONE table (pure, node-testable);
 * the banner (components/nav/EntityScreen.jsx), the chip's "same screen or the entity's page" choice and
 * docs/NAV2_NOTES.md all read it. Evidence = the existing API parameter the screen passes (file:line in the notes).
 *
 *   FILTERS      — the screen's API already accepts an entity filter, applied INSIDE the caller's existing scope
 *                  (member visibility engine / VISIBILITY1 staff scope). NAV2 only passes it; no new visibility path.
 *   NOT_FILTERED — the screen shows "This screen isn't filtered by entity" while an entity is selected: no silent
 *                  mismatch. Each carries the reason (and what nav3 needs).
 */
import { canAccess, GATE_CRM } from "./menuVisibility.mjs";
import { destinationFor } from "./entityContext.mjs";

export const FILTERS = "filters";
export const NOT_FILTERED = "not_filtered";

/** href → { mode, how, why }. `mode` may be a function of the query string (the /portfolio tabs). */
export const SCREENS = {
  // ── Cross Book ──
  "/portfolio": {
    mode: (sp) => (["allocation", "gap"].includes(sp.get("tab") || "") ? FILTERS : NOT_FILTERED),
    how: "tab=allocation|gap → GET /portfolio/allocations?entity_id (require_entity_access)",
    why: "Investments tab: GET /portfolio/my-investments takes no entity (the caller's own investments)",
  },
  "/portfolio/positions": { mode: FILTERS, how: "GET /portfolio/positions?owner_entity_id (ANDed with member_entity_scope)" },
  "/portfolio/transactions": { mode: FILTERS, how: "GET /portfolio/transactions?owner_entity_id (ANDed with member_entity_scope)" },
  "/portfolio/allocation": { mode: FILTERS, how: "GET /allocation-lens?selector_type=entity&entity_id (require_entity_access)" },
  "/portfolio/ownership-tree": { mode: NOT_FILTERED, why: "GET /me/ownership-graph?focal_id is accepted only inside the caller's OWN delegate tree (services/ownership_tree.member_tree → 403 otherwise): staff choosing a visible client would get a refusal — nav3 (staff use the CRM graph)" },
  "/portfolio/securities": { mode: NOT_FILTERED, why: "GET /portfolio/securities has no entity param (scope is internal: list_assets(entity_scope)) — nav3" },
  "/portfolio/commitments": { mode: NOT_FILTERED, why: "GET /portfolio/commitments has no entity param (visible_entity_ids internal) — nav3" },
  "/portfolio/households": { mode: NOT_FILTERED, why: "household lists have no entity param; households are their own id space (household_memberships) — nav3" },
  "/portfolio/approvals": { mode: NOT_FILTERED, why: "GET /approvals/bulk filters by household_id (households.id, not an entity id) — nav3 maps entity → household" },
  // ── Model Portfolios ──
  "/portfolio/models": { mode: NOT_FILTERED, why: "the org's model library — not tied to an entity" },
  "/portfolio/replication/catalog": { mode: NOT_FILTERED, why: "the provider model catalog — not tied to an entity" },
  "/portfolio/replication": { mode: NOT_FILTERED, why: "the org's replica registry — not tied to an entity" },
  "/portfolio/replication/tilts": { mode: NOT_FILTERED, why: "account-scoped (?account) — nav3 maps entity → accounts" },
  "/portfolio/replication/backtest": { mode: NOT_FILTERED, why: "GET /replication/backtests has no entity/household param (rows carry household_id) — nav3" },
  "/portfolio/accounts": { mode: NOT_FILTERED, why: "GET /proposals/accounts has no household/entity param — nav3" },
  "/portfolio/tax": { mode: NOT_FILTERED, why: "GET /tax/overview has no entity param (groups by household internally) — nav3" },
  "/portfolio/replication/section351": { mode: NOT_FILTERED, why: "candidates are account-based, no list filter — nav3" },
  "/portfolio/blocks": { mode: NOT_FILTERED, why: "blocks span accounts — not tied to one entity" },
  "/portfolio/instructions": { mode: NOT_FILTERED, why: "GET /execution/instructions takes account_id only — nav3 (household join exists)" },
  // ── Treasury ──
  "/treasury": { mode: NOT_FILTERED, why: "keyed by ?household (households.id, a different id space) — nav3 maps entity → household" },
  // ── Documents ──
  "/documents": { mode: NOT_FILTERED, why: "GET /document-search has no entity param (visible_entity_ids internal) — nav3" },
  "/documents/semantic": { mode: NOT_FILTERED, why: "GET /semantic-search has no entity param (visible_entity_ids internal) — nav3" },
  // ── Structured Notes ──
  "/portfolio/replication/notes": { mode: NOT_FILTERED, why: "the note catalog — not tied to an entity" },
  "/portfolio/replication/notes/new-issues": { mode: NOT_FILTERED, why: "new note issues — not tied to an entity" },
};

/** The screen's mode for a path (+ query): FILTERS | NOT_FILTERED, or null when the path is not a book screen. */
export function screenMode(pathname, search = "") {
  const s = SCREENS[pathname];
  if (!s) return null;
  if (typeof s.mode === "function") {
    let sp;
    try {
      sp = new URLSearchParams(typeof search === "string" ? search : "");
    } catch {
      sp = new URLSearchParams("");
    }
    return s.mode(sp);
  }
  return s.mode;
}

/**
 * What a screen shows for a chip state: { banner: "filtered" | "not_filtered" | null, entity } — `entity` is the one
 * the screen may filter on (only a VISIBLE entity on a FILTERS screen). A not-available id = nothing selected.
 */
export function screenDecision(pathname, search, state) {
  const mode = screenMode(pathname, search);
  const visible = state && state.kind === "entity";
  if (!mode || !visible) return { banner: null, entity: null, mode };
  if (mode === FILTERS) {
    return { banner: "filtered", entity: { id: state.id, display_name: state.name, entity_type: state.type }, mode };
  }
  return { banner: "not_filtered", entity: null, mode };
}

/**
 * Where choosing an entity (chip or Ctrl K) takes `me`: the same screen with entity_id when the screen filters by
 * entity, else the entity's own CRM page when `me` may open CRM (GATE_CRM — unchanged), else the Cross Book
 * overview. ONE rule for both entry points.
 */
export function entityDestination({ me, pathname, search, entityId }) {
  return destinationFor({
    pathname, search, entityId,
    screenFilters: screenMode(pathname, search) === FILTERS,
    canOpenRecord: canAccess(me, GATE_CRM),
  });
}
