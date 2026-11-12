// ALTREAD1 — the pure logic behind the account page's custodian section (the custodian's balance as of its date, its
// tax lots from the cost-basis feed, the reconciliation against Hollisworks' lots, the lot source in effect) and the
// connection status's refresh-token expiry. Everything is decided HERE from the server's envelope and fails CLOSED:
// nothing renders without `permissions.can_read === true`; there is no write control on this panel at all (the lot
// source is changed through the lot-method change, which keeps its own gate). Labels come from the envelope.

const listOf = (x) => (Array.isArray(x) ? x : []);

/** True only for an envelope the server says the caller may read. */
export function canShow(envelope) {
  return Boolean(envelope && typeof envelope === "object" && envelope.permissions?.can_read === true);
}

/** "Custodian balance (as of 2026/11/10)" lines — one per feed source, the server's own strings. */
export function balanceLines(envelope) {
  return listOf(envelope?.balances)
    .filter((b) => b && typeof b.account_balance === "string" && typeof b.as_of_date === "string")
    .map((b) => ({ key: `${b.source_system}:${b.as_of_date}`, amount: b.account_balance, asOf: b.as_of_date,
      label: `Custodian balance (as of ${b.as_of_date})`, status: b.custodian_status || null,
      simulated: b.source_system === "altruist_simulator" }));
}

export function lotRows(envelope) {
  return listOf(envelope?.lots).filter((l) => l && typeof l.external_id === "string");
}

/** Reconciliation rows; ``differs`` drives the highlight (any status but "match"). */
export function reconRows(envelope) {
  return listOf(envelope?.reconciliation?.rows).filter((r) => r && typeof r.key === "string")
    .map((r) => ({ ...r, differs: r.status !== "match" }));
}

/** The lot source line: the server's label for the effective value (default: Hollisworks-computed lots). */
export function lotSourceText(envelope) {
  const key = envelope?.lot_source;
  const hit = listOf(envelope?.vocabularies?.lot_sources).find((x) => x?.key === key);
  return hit ? hit.label : (envelope?.lot_source_label || key || "");
}

/** A connection's refresh-token expiry line (documented 28-day lifetime), or null when nothing to say. */
export function refreshExpiryText(conn) {
  if (!conn || typeof conn !== "object") return null;
  const at = typeof conn.refresh_token_expires_at === "string" ? conn.refresh_token_expires_at.slice(0, 10) : null;
  if (conn.refresh_token_expired === true) {
    return { expired: true, text: `Refresh token expired${at ? ` ${at}` : ""} — reconnect to authorize again` };
  }
  return at ? { expired: false, text: `Refresh token expires ${at}` } : null;
}
