// DISCLOSURES1 — the disclosure library's pure logic behind the screens. Every control is decided HERE from the
// server's envelope and FAILS CLOSED: a control shows only for `=== true` on the server's own permission flag AND an
// action the server listed (on the envelope or on the version). A missing, partial or malformed envelope shows none.
// Every label comes from the envelope's vocabularies; nothing here holds disclosure text.

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);
const has = (obj, a) => listOf(obj?.actions).includes(a);

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

export function rowsOf(envelope) {
  return listOf(envelope?.rows);
}

/** Upload a new version: can_write + the envelope's "upload" (or "upload_override") action + "upload" editable. */
export function uploadControls(envelope) {
  const w = isTrue(perms(envelope).can_write);
  const editable = listOf(vocab(envelope).editable);
  const scopes = listOf(vocab(envelope).manageable_scopes);
  const allowed = w && editable.includes("upload") && (has(envelope, "upload") || has(envelope, "upload_override"));
  return { upload: allowed && scopes.length > 0, scopes: allowed ? scopes : [] };
}

/** Per version: publish / retire only where the server listed it on THAT version (never for its uploader). */
export function versionControls(envelope, version) {
  const w = isTrue(perms(envelope).can_write);
  return { publish: w && has(version, "publish"), retire: w && has(version, "retire") };
}

/** "Why this is in force": the server's own words (platform default / org override / built-in text). */
export function inForceText(row) {
  const f = row?.in_force;
  if (!f || typeof f !== "object") return "";
  const v = f.version_no ? ` — version ${f.version_no}` : "";
  return `${f.why || row?.why || ""}${v}`;
}

/** The summary shown inline — the server's text, or "" (a caller renders nothing rather than a placeholder). */
export function summaryOf(row) {
  const t = row?.in_force?.text;
  return typeof t === "string" ? t : "";
}

/** The body the upload route takes (JSON + base64); the caller supplies the file's bytes as base64. */
export function uploadBody({ scope, file_name, content_base64, summary, effective_from, household_id, account_id,
  note }) {
  const b = { scope, file_name, content_base64, summary };
  if (effective_from) b.effective_from = effective_from;
  if (household_id) b.household_id = household_id;
  if (account_id) b.account_id = account_id;
  if (note) b.note = note;
  return b;
}

/** A file's extension is one the server accepts (vocabularies.file_types) and its size is within max_bytes. */
export function fileAccepted(envelope, name, size) {
  const v = vocab(envelope);
  const ext = String(name || "").split(".").pop().toLowerCase();
  const types = listOf(v.file_types);
  const okType = types.includes(ext) || (ext === "htm" && types.includes("html"));
  return Boolean(okType && Number.isFinite(size) && size > 0 && size <= Number(v.max_bytes || 0));
}
