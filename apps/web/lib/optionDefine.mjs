// CATCHALL1 Task 5 (MUSE I-12) — "Define this contract" from the held-away option line's picker. Pure helpers: the
// control renders only when the picker's envelope grants it (can_write + editable "define_contract" — rep8's own
// manage_replication gate, published by the API); the request is rep8's ContractIn body (POST
// /api/replication/options/contracts → options_sleeve.find_or_create_contract, idempotent on its terms — the same
// terms again RETURN the existing contract, never a second one). Strike / multiplier stay strings (exact Decimal).

export function canDefine(env) {
  if (env?.permissions?.can_write !== true) return false;
  const ed = env?.vocabularies?.editable;
  return Array.isArray(ed) && ed.includes("define_contract") && Boolean(env?.underlying?.global_security_id);
}

/** The form's starting values, from the envelope's defaults (never a client-side guess of the rules). */
export function defineForm(env) {
  const v = env?.vocabularies || {};
  return { option_type: "call", strike: "", expiry: "", multiplier: v.default_multiplier || "100",
    exercise_style: v.default_exercise_style || "", settlement: v.default_settlement || "",
    broad_based_index: false };
}

/** Problems the form can name before sending (the API still decides every rule). */
export function defineProblems(form) {
  const out = [];
  if (!["call", "put"].includes(form?.option_type)) out.push("Choose call or put");
  if (!/^\d+(\.\d+)?$/.test(String(form?.strike || "").trim())) out.push("Strike is a number, e.g. 660");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(form?.expiry || ""))) out.push("Expiry is a date");
  if (!/^\d+(\.\d+)?$/.test(String(form?.multiplier || "").trim())) out.push("Multiplier is a number (usually 100)");
  if (!form?.exercise_style) out.push("Choose the exercise style");
  return out;
}

/** rep8's ContractIn body for the picker's underlying. */
export function defineBody(env, form) {
  const kind = env?.underlying?.kind;
  return {
    underlying_global_security_id: env.underlying.global_security_id,
    option_type: form.option_type,
    strike: String(form.strike).trim(),
    expiry: form.expiry,
    exercise_style: form.exercise_style,
    settlement: form.settlement || (kind === "index" ? "cash" : "physical"),
    is_flex: false,
    multiplier: String(form.multiplier || "100").trim(),
    exchange_listed: true,
    broad_based_index: kind === "index" ? Boolean(form.broad_based_index) : false,
    is_mini: false,
  };
}

/** The picker row for a contract rep8 returned ({contract}), and whether it was already among the rows. */
export function pickedFromDefine(env, response) {
  const c = response?.contract;
  if (!c) return { row: null, existed: false };
  const row = { global_security_id: c.global_security_id, option_type: c.option_type, strike: c.strike,
    expiry: c.expiry, multiplier: c.multiplier, occ_symbol: c.occ_symbol, exercise_style: c.exercise_style };
  const rows = Array.isArray(env?.rows) ? env.rows : [];
  return { row, existed: rows.some((r) => r.global_security_id === c.global_security_id) };
}

/** Rows with the defined contract added once (a duplicate definition never adds a second line). */
export function withContract(rows, row) {
  const list = Array.isArray(rows) ? rows : [];
  if (!row || list.some((r) => r.global_security_id === row.global_security_id)) return list;
  return [...list, row];
}
