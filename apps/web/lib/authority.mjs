// REP7B — account authority (read-only | advise | managed) and custody: the
// pure logic behind the Authority panels, the one-off trade form and the
// X-ray's "Propose a one-off trade". Every control is decided HERE from the
// server's envelope and fails CLOSED: a control shows only for `=== true` on
// the server's own flag AND an action the server listed. Labels and the
// authority × custody matrix come from the envelope's vocabularies — nothing
// here restates them. Nothing here shows billing (an open decision).

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

/** "Request a change" on a household or account Authority panel. */
export function requestControl(envelope) {
  return isTrue(perms(envelope).can_write) && listOf(envelope?.actions).includes("request_change")
    && listOf(vocab(envelope).editable).length > 0;
}

/** Approve / Reject on a pending change — approve_authority AND listed on the change (never its maker). */
export function changeControls(envelope, change) {
  const ap = isTrue(perms(envelope).can_approve);
  const a = listOf(change?.actions);
  return { approve: ap && a.includes("approve"), reject: ap && a.includes("reject") };
}

/** The authority options a request may choose, from the server's vocabulary. */
export function authorityOptions(envelope) {
  return listOf(vocab(envelope).authorities).filter((x) => x && typeof x.key === "string");
}

/** Custody options; the matrix marks managed + outside invalid — the form never offers that pair. */
export function custodyOptions(envelope, authority) {
  const m = listOf(vocab(envelope).matrix);
  return listOf(vocab(envelope).custodies).filter((c) => {
    if (!authority) return true;
    const cell = m.find((x) => x.authority === authority && x.custody === c.key);
    return !cell || cell.valid === true;
  });
}

/** Receiving-custodian fields only for another custodian (the server refuses them otherwise). */
export function needsReceiving(custody) {
  return custody === "other_custodian";
}

/** The account page's "Propose a one-off trade" — manage AND the server's listed action (advise only). */
export function oneOffAccountControl(envelope) {
  return isTrue(perms(envelope).can_write) && listOf(envelope?.actions).includes("propose_one_off");
}

/** The X-ray line's "Propose a one-off trade" — the join permission AND the server's action AND advise. */
export function oneOffXrayControl(envelope, fund) {
  return isTrue(perms(envelope).can_join) && listOf(fund?.actions).includes("propose_one_off")
    && fund?.one_off?.available === true;
}

/** The executor choices for a one-off (advise): only what the server's vocabulary lists. */
export function oneOffExecutors(envelope) {
  return listOf(vocab(envelope).one_off_executors).filter((e) => e && typeof e.key === "string");
}

/** A request body for an account change; receiving details only for another custodian, last four only. */
export function accountChangeBody(form) {
  const body = {
    authority_override: form.authority_override || null, custody: form.custody,
    effective_from: form.effective_from, reason: (form.reason || "").trim(),
  };
  if (needsReceiving(form.custody)) {
    const r = form.receiving || {};
    body.receiving = {
      custodian_name: (r.custodian_name || "").trim() || undefined,
      participant_number: (r.participant_number || "").trim() || undefined,
      agent_bank: (r.agent_bank || "").trim() || undefined,
      account_last4: (r.account_last4 || "").trim().slice(-4) || undefined,
      contra_broker: (r.contra_broker || "").trim() || undefined,
    };
  }
  return body;
}

export function sourceText(envelope, eff) {
  return labelOf(vocab(envelope).sources, eff?.authority_source);
}
