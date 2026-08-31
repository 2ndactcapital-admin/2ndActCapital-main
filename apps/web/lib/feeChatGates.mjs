/**
 * The fee-chat screen's write gates. Pure, importable from Node.
 *
 * Extracted from FeeChatWorkbench.jsx for the same reason lib/menuVisibility.mjs
 * was extracted from the sidebar: a JSX component cannot be rendered in a plain
 * Node harness without a transform, so the DECISION lives here and the
 * component does nothing but call it. A harness exercising this module is
 * therefore exercising the shipped rule, not a re-implementation that agrees
 * with itself.
 *
 * THE ONE RULE THESE ENCODE
 * ──────────────────────────────────────────────────────────────────────────
 * A missing or malformed envelope must fail CLOSED. Every function below
 * returns the LOCKED answer (false, or an empty list) for null, undefined, a
 * partial object, or anything that is not the shape the server publishes.
 * There is no `|| DEFAULTS` anywhere, because that is exactly the pattern that
 * silently restores full write access the moment the envelope goes missing for
 * an unrelated reason — a dropped fetch, a 500, a renamed key.
 *
 * `can_write === true` is an identity check, not a truthiness check. The string
 * "false" is truthy in JavaScript, and a permissions object that arrived as
 * form data or survived a sloppy serialisation round trip would otherwise grant
 * write access on the literal word "false".
 */

/** True only for an envelope that explicitly says so. */
export function canWrite(permissions) {
  return permissions?.can_write === true;
}

/** True only for an envelope that explicitly says so. */
export function canRead(permissions) {
  return permissions?.can_read === true;
}

/**
 * The fields this caller may edit.
 *
 * The server already empties `editable` for a view-only caller. This intersects
 * it with `can_write` anyway: the two come from the same response, but they are
 * two separate assertions, and a screen that trusted only one of them would
 * render inputs for a caller the API will refuse — a visible button behind an
 * unprotected endpoint and a protected endpoint behind a visible button are
 * both real bugs.
 */
export function editableFields(permissions, vocabularies) {
  if (!canWrite(permissions)) return [];
  const editable = vocabularies?.editable;
  if (!Array.isArray(editable)) return [];
  return editable.filter((f) => typeof f === "string" && f.length > 0);
}

/** May this specific field render an input rather than static text? */
export function mayEditField(permissions, vocabularies, field) {
  return editableFields(permissions, vocabularies).includes(field);
}

/** Does the save control render at all? */
export function showSaveControl(permissions) {
  return canWrite(permissions);
}

/**
 * Is the save actually enabled?
 *
 * Separate from {@link showSaveControl} on purpose: "you may not save" and
 * "this schedule is not ready" are different messages, and collapsing them
 * would tell a view-only advisor their schedule is invalid.
 */
export function saveEnabled(permissions, validationErrors) {
  if (!canWrite(permissions)) return false;
  return Array.isArray(validationErrors) && validationErrors.length === 0;
}

/** The choices for a vocabulary field, or none. Never a client-side default. */
export function choicesFor(vocabularies, field) {
  const values = vocabularies?.values;
  if (!values || typeof values !== "object") return null;
  const choices = values[field];
  return Array.isArray(choices) && choices.length ? choices : null;
}
