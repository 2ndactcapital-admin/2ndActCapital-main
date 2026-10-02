// noteextractb1 — the gold review screen's write gate, in a pure module so the
// verify can feed the REAL API envelope into the exact rule the component
// renders from. Fails CLOSED: a missing or malformed envelope, a can_write that
// is not literally true, or an editable list without "value" all mean no
// write controls. There is no default list and no truthy fallback.
export function canWriteGold(payload) {
  return payload?.permissions?.can_write === true
    && Array.isArray(payload?.vocabularies?.editable)
    && payload.vocabularies.editable.includes("value");
}
