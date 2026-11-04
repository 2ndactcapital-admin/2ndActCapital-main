// REP12 — the note-versus-replica check across new issues: the pure logic
// behind Replication → Notes → New issues and the Super Admin's underlying
// queue. Every control is decided HERE from the server's envelope and FAILS
// CLOSED: a control shows only for `=== true` on the server's own flag AND an
// action the server listed. A missing, partial or malformed envelope shows
// none. Every label comes from the envelope's vocabularies. An embedded-fee
// figure is an ESTIMATE shown only as the server sends it — "not priced — …"
// naming what is missing otherwise, never a placeholder number.

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);

export function perms(envelope) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  return p && typeof p === "object" ? p : {};
}

export function vocab(envelope) {
  const v = envelope && typeof envelope === "object" ? envelope.vocabularies : null;
  return v && typeof v === "object" ? v : {};
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

const has = (obj, a) => listOf(obj?.actions).includes(a);

/** The feed's page-level controls: "check now" needs can_write and the listed action; the settings form needs
 * can_write and a non-empty editable vocabulary. */
export function feedControls(envelope) {
  const w = isTrue(perms(envelope).can_write);
  return { run: w && has(envelope, "run"), settings: w && listOf(vocab(envelope).editable).length > 0 };
}

/** One feed row: review / dismiss need can_review and the action the server listed on THAT row. */
export function rowControls(envelope, row) {
  const r = isTrue(perms(envelope).can_review);
  return { review: r && has(row, "review"), dismiss: r && has(row, "dismiss") };
}

/** The underlying queue: confirm / reject need can_confirm_underlyings (Super Admin) and the item's own action. */
export function queueControls(envelope, item) {
  const c = isTrue(perms(envelope).can_confirm_underlyings);
  return { confirm: c && has(item, "confirm"), reject: c && has(item, "reject") };
}

/** The estimate cell exactly as the server states it ("… per 100 — estimate" or "not priced — needs …"). */
export function estimateText(row) {
  const e = row && typeof row === "object" ? row.estimate : null;
  if (!e || typeof e !== "object") return "—";
  return typeof e.text === "string" && e.text ? e.text : "—";
}

/** True only when the server says the row is priced (sorting / emphasis never guesses). */
export function isPriced(row) {
  return isTrue(row?.estimate?.priced);
}

/** The feed's query string from the filter form (empty fields dropped). */
export function feedQuery(f) {
  const q = new URLSearchParams();
  for (const k of ["verdict", "archetype", "issuer", "underlying", "date_from", "date_to", "status", "sort"]) {
    const v = f && typeof f[k] === "string" ? f[k].trim() : "";
    if (v) q.set(k, v);
  }
  return q.toString();
}

/** The ids of the one-proposal edges a Super Admin ticked (only those the server offers "confirm" on). */
export function confirmable(envelope, selected) {
  const items = listOf(envelope?.groups?.one_proposal);
  const ok = new Set(items.filter((i) => queueControls(envelope, i).confirm).map((i) => i.id));
  return listOf(selected).filter((id) => ok.has(id));
}

/** Comma-separated text → a trimmed list (settings watches). */
export function splitList(text) {
  return String(text || "").split(",").map((s) => s.trim()).filter(Boolean);
}
