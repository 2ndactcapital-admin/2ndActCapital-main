/**
 * lib/market/customDates.mjs (mkt04c Task 2): the add form's enabling rule
 * (a trimmed name and a date — every other rule is the server's), the exact
 * request, the server's message shown verbatim with the inputs kept on a 422
 * or 409, nothing optimistic, and the two-step delete.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CLOSED_FORM,
  CUSTOM_DATES_ROUTE,
  SAVE_FAILED,
  afterDelete,
  afterSubmit,
  canSave,
  canWriteDates,
  closeForm,
  confirmDelete,
  customDateUrl,
  deleteConfirmText,
  editForm,
  nameMaxLength,
  openForm,
  refusalMessage,
  requestDelete,
  submitForm,
} from "../../lib/market/customDates.mjs";
import { interpretKeyDates } from "../../lib/market/chartRequest.mjs";
import { keyDates04cBody } from "./mkt04cFixtures.mjs";

// The backend's own refusal shapes (services/market_data/personal.py refuse):
// the message is repeated as the one field error.
const refusal = (msg, status = 422) => ({ ok: false, status, body: { detail: { message: msg, errors: [{ loc: ["body", "name"], type: "x", msg }] } } });

test("Save needs a non-empty trimmed name and a date — nothing else is checked in the browser", () => {
  let f = openForm();
  assert.equal(canSave(f), false);
  f = editForm(f, "name", "   ");
  f = editForm(f, "date", "2020-01-01");
  assert.equal(canSave(f), false, "a blank name is not a name");
  f = editForm(f, "name", " x ");
  assert.equal(canSave(f), true);
  assert.equal(canSave(editForm(f, "date", "")), false, "a date is required");
  // Rules the SERVER owns are not re-implemented: a far-future date or a very
  // long name still enables Save, and the server answers.
  assert.equal(canSave(editForm(editForm(f, "date", "2999-12-31"), "name", "n".repeat(500))), true);
  assert.equal(canSave(CLOSED_FORM), false, "a closed form cannot save");
  assert.equal(editForm(f, "org_id", "x"), f, "only name and date are form fields");
});

test("the request is exactly POST /api/market/key-dates/custom {name, event_date}", () => {
  const f = editForm(editForm(openForm(), "name", "My event"), "date", "2021-02-03");
  const step = submitForm(f);
  assert.deepEqual(step.request, { url: CUSTOM_DATES_ROUTE, method: "POST", body: { name: "My event", event_date: "2021-02-03" } });
  assert.equal(CUSTOM_DATES_ROUTE, "/api/market/key-dates/custom");
  assert.equal(step.form.saving, true);
  assert.equal(submitForm(step.form), null, "no double submit while saving");
  assert.equal(submitForm(openForm()), null, "nothing is sent while Save is disabled");
});

test("a 422 or 409 shows the server's message ONCE and keeps the inputs", () => {
  const f = submitForm(editForm(editForm(openForm(), "name", "Dup"), "date", "2021-02-03")).form;
  for (const [msg, status] of [
    ["Server says duplicate text 409", 409],
    ["Server says out of range text 422", 422],
  ]) {
    const out = afterSubmit(f, refusal(msg, status));
    assert.equal(out.created, null, "nothing was created");
    assert.equal(out.form.error, msg, "verbatim, not repeated as 'm: m'");
    assert.equal(out.form.name, "Dup");
    assert.equal(out.form.date, "2021-02-03");
    assert.equal(out.form.open, true);
    assert.equal(out.form.saving, false);
  }
  // The limit refusal carries no field error (refuse(MSG, None, ...)).
  assert.equal(afterSubmit(f, { ok: false, body: { detail: { message: "Limit text" } } }).form.error, "Limit text");
  // A field error that differs from the headline is kept.
  assert.equal(
    refusalMessage({ detail: { message: "Head", errors: [{ msg: "Other" }] } }, "fb"),
    "Head: Other",
  );
  // No body at all (network) -> the page's own fallback.
  assert.equal(afterSubmit(f, { ok: false, body: null }).form.error, SAVE_FAILED);
});

test("success: the form closes and the SERVER's row is handed back (nothing invented)", () => {
  const f = submitForm(editForm(editForm(openForm(), "name", "New"), "date", "2021-02-03")).form;
  const row = { id: "44444444-0000-0000-0000-000000000001", name: "New", event_date: "2021-02-01", created_at: "x", updated_at: "x" };
  const out = afterSubmit(f, { ok: true, status: 201, body: row });
  assert.deepEqual(out.form, closeForm());
  assert.equal(out.created, row, "the server's own object — its date, not the typed one");
  // A 201 without a usable row is not a success.
  assert.equal(afterSubmit(f, { ok: true, body: { deleted: true } }).created, null);
});

test("delete takes two steps: asking sends nothing; confirming builds the DELETE; a refusal keeps the confirm row", () => {
  const id = "11111111-0000-0000-0000-000000000001";
  const asked = requestDelete(id);
  assert.deepEqual(asked, { id, busy: false, error: null });
  const step = confirmDelete(asked);
  assert.deepEqual(step.request, { url: `/api/market/key-dates/custom/${id}`, method: "DELETE" });
  assert.equal(confirmDelete(step.pending), null, "no second DELETE while one is in flight");
  assert.equal(confirmDelete(null), null);
  assert.deepEqual(afterDelete(step.pending, { ok: true, body: { deleted: true } }), { pending: null, deleted: true });
  const refused = afterDelete(step.pending, { ok: false, body: { detail: { message: "Not found." } } });
  assert.equal(refused.deleted, false);
  assert.equal(refused.pending.error, "Not found.");
  assert.equal(deleteConfirmText("Personal alpha"), 'Delete "Personal alpha" from your dates?');
  assert.equal(customDateUrl("a/b"), "/api/market/key-dates/custom/a%2Fb", "the id is encoded, nothing else is added");
});

test("write controls need the envelope's can_write === true; the name limit is the server's", () => {
  assert.equal(canWriteDates(interpretKeyDates({ ok: true, body: keyDates04cBody() })), true);
  for (const mutate of [
    (b) => (b.permissions.can_write = false),
    (b) => (b.permissions.can_write = "true"),
    (b) => delete b.permissions.can_write,
    (b) => delete b.permissions,
  ]) {
    const b = keyDates04cBody();
    mutate(b);
    assert.equal(canWriteDates(interpretKeyDates({ ok: true, body: b })), false);
  }
  assert.equal(canWriteDates({ kind: "loading" }), false);
  assert.equal(nameMaxLength({ name_max: 60 }), 60);
  assert.equal(nameMaxLength({}), undefined, "no limit published -> no attribute, never a typed number");
});
