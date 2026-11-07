// ROLLUP2 — pure helpers for the net-worth surfaces (entity card, household summary). Every label comes from the
// server's envelope (vocabularies); nothing here invents a figure — it only picks what the API computed.

function listOf(v) {
  return Array.isArray(v) ? v : [];
}

function labelOf(list, key, fallback = key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : fallback;
}

/** The Unclassified label from the envelope (services.networth.vocabularies). */
export function unclassifiedLabel(vocab) {
  const u = vocab && typeof vocab === "object" ? vocab.unclassified : null;
  return u && typeof u === "object" && u.label ? u.label : "Unclassified";
}

/** Family / Charitable / Household total, in the envelope's order with its labels; null when the API sent no split. */
export function splitRows(total, vocab) {
  const s = total && typeof total === "object" ? total.split : null;
  if (!s || typeof s !== "object") return null;
  const lines = listOf(vocab?.split_lines);
  const keys = lines.length ? lines.map((l) => l.key) : ["family", "charitable", "household"];
  return keys.filter((k) => s[k] && typeof s[k] === "object").map((k) => ({
    key: k, label: labelOf(lines, k), gross: s[k].gross, liabilities: s[k].liabilities, net: s[k].net,
    unclassified: s[k].unclassified_value,
    entities: k === "charitable" ? listOf(s[k].entities).map((e) => e.display_name).filter(Boolean) : [],
  }));
}

/** The household's Owners list: every member's own card figure (the server computed both from one core). */
export function ownerRows(total) {
  return listOf(total?.owners).map((o) => ({
    id: o.entity_id, name: o.display_name, charitable: o.charitable === true, gross: o.gross,
    liabilities: o.liabilities, net: o.net, unclassified: Number(o.unclassified_value) ? o.unclassified_value : null,
  }));
}

/** The valued holdings with no allocation bucket — counted in net worth, listed so an operator can classify them. */
export function unclassifiedRows(netWorth) {
  return listOf(netWorth?.unclassified).map((r) => ({ id: r.position_id, label: r.label, owner: r.owner_name,
    value: r.value, asOf: r.as_of_date }));
}

/** The holdings with no value — NOT counted, listed with the reason. */
export function skippedRows(netWorth) {
  return listOf(netWorth?.skipped).map((r) => ({ id: r.position_id, label: r.label, owner: r.owner_name,
    reason: r.reason }));
}

/** ROLLUP2 (MUSE F5-11) — an ownership-tree percentage. A direct entry is the root's own stake; a nested entry is the
 * stake of the entity above it, so it says whose ("60.00% via Olivo Holdings LP" / "80.00% of OH GP LLC"). */
export function treePctLabel(pct, via, direction = "down") {
  const n = Number(pct);
  if (pct === null || pct === undefined || Number.isNaN(n)) return "";
  const p = `${n.toFixed(2)}%`;
  if (!via) return p;
  return direction === "down" ? `${p} via ${via}` : `${p} of ${via}`;
}

/** ROLLUP2 Task 4 — the Ownership panel's duplicate warnings (the server's own text; none when the envelope has none). */
export function duplicateWarnings(ownershipEnvelope) {
  return listOf(ownershipEnvelope?.duplicates).map((d) => ({ key: `${d.parent_id}-${d.child_id}`, text: d.warning }))
    .filter((w) => w.text);
}
