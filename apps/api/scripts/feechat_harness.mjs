/**
 * Hermetic Node harness for the REAL fee-chat write gates.
 *
 * Imports apps/web/lib/feeChatGates.mjs directly — the same module
 * components/fee/FeeChatWorkbench.jsx imports and calls for every write control
 * it renders. Nothing is re-implemented here, so a pass means the shipped rule
 * behaves as asserted rather than that a test agrees with itself. Same
 * discipline as menuvisibility_harness.mjs.
 *
 * This is the CLIENT half of the permission proof. verify_fee40.py proves the
 * server half independently — that the API itself refuses the write. A hidden
 * button behind an unprotected endpoint and a protected endpoint behind a
 * visible button are both real bugs, and proving one says nothing about the
 * other.
 *
 * Prints one JSON object on stdout; verify_fee40.py reads it.
 */

import {
  canRead,
  canWrite,
  choicesFor,
  editableFields,
  mayEditField,
  saveEnabled,
  showSaveControl,
} from "../../web/lib/feeChatGates.mjs";

// ── Fixtures — shaped exactly like routers/fee_chat's own envelope ──────────

// A view-only advisor: passed the read gate, holds no manage_billing. The
// server empties `editable` for exactly this caller.
const VIEW_ONLY = {
  permissions: {
    can_read: true,
    can_write: false,
    is_super_admin: false,
    read_permission: "view_portfolio",
    write_permission: "manage_billing",
  },
  vocabularies: {
    editable: [],
    inline_editable: [],
    values: { valuation_method: ["PERIOD_END", "AVG_DAILY"] },
  },
};

const WRITER = {
  permissions: {
    can_read: true,
    can_write: true,
    is_super_admin: false,
    read_permission: "view_portfolio",
    write_permission: "manage_billing",
  },
  vocabularies: {
    editable: ["valuation_method", "billing_frequency"],
    inline_editable: ["valuation_method", "billing_frequency"],
    values: { valuation_method: ["PERIOD_END", "AVG_DAILY"] },
  },
};

// The failure modes a lost envelope actually takes in production: a dropped
// fetch, a 500 body, a partial object, and the serialisation round trip that
// turns false into the (truthy!) string "false".
const LOST = [
  ["null envelope", null],
  ["undefined envelope", undefined],
  ["empty object", {}],
  ["no can_write key", { can_read: true }],
  ["string 'false'", { can_read: true, can_write: "false" }],
  ["string 'true'", { can_read: true, can_write: "true" }],
  ["number 1", { can_read: true, can_write: 1 }],
];

const out = {};

// ── The view-only caller renders NO write control at all ───────────────────
out.view_only_can_write = canWrite(VIEW_ONLY.permissions);
out.view_only_can_read = canRead(VIEW_ONLY.permissions);
out.view_only_editable = editableFields(VIEW_ONLY.permissions, VIEW_ONLY.vocabularies);
out.view_only_may_edit_valuation = mayEditField(
  VIEW_ONLY.permissions,
  VIEW_ONLY.vocabularies,
  "valuation_method",
);
out.view_only_save_control = showSaveControl(VIEW_ONLY.permissions);
out.view_only_save_enabled = saveEnabled(VIEW_ONLY.permissions, []);

// ── The writer does, so the checks above are gates and not dead code ───────
out.writer_can_write = canWrite(WRITER.permissions);
out.writer_editable = editableFields(WRITER.permissions, WRITER.vocabularies);
out.writer_may_edit_valuation = mayEditField(
  WRITER.permissions,
  WRITER.vocabularies,
  "valuation_method",
);
out.writer_may_edit_unlisted = mayEditField(
  WRITER.permissions,
  WRITER.vocabularies,
  "minimum_fee", // held by the server as NOT editable for this caller
);
out.writer_save_control = showSaveControl(WRITER.permissions);
out.writer_save_enabled_clean = saveEnabled(WRITER.permissions, []);
out.writer_save_enabled_with_errors = saveEnabled(WRITER.permissions, [
  { code: "tier_gap" },
]);

// ── Every lost-envelope shape must fail CLOSED ─────────────────────────────
out.lost_envelope = LOST.map(([label, permissions]) => ({
  label,
  can_write: canWrite(permissions),
  save_control: showSaveControl(permissions),
  save_enabled: saveEnabled(permissions, []),
  editable: editableFields(permissions, WRITER.vocabularies),
  may_edit: mayEditField(permissions, WRITER.vocabularies, "valuation_method"),
}));

// A writer whose vocabularies went missing still renders no inputs: the two
// halves of the envelope are separate assertions.
out.writer_no_vocabularies = editableFields(WRITER.permissions, null);
out.writer_bad_vocabularies = editableFields(WRITER.permissions, { editable: "all" });

// Choices are never defaulted client-side.
out.choices_present = choicesFor(WRITER.vocabularies, "valuation_method");
out.choices_absent = choicesFor(WRITER.vocabularies, "margin_treatment");
out.choices_no_envelope = choicesFor(null, "valuation_method");

process.stdout.write(JSON.stringify(out, null, 2));
