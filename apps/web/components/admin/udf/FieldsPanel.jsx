"use client";

// UDFUI1 — Fields: every definition (any status) by record type, filters, create / edit with exactly the options the
// definition schema supports (server-published type_params rules), and the lifecycle actions the row's status allows.
// Write controls render ONLY when the envelope's permissions.can_write is true (fail closed).
import { useState } from "react";
import DataGrid from "@/components/ui/DataGrid";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import {
  ACTION_LABELS, canWrite, definitionActions, definitionCreateBody, definitionPatchBody, filterDefinitions, label,
  paramRules, placementText, slugKey, validateDefinitionForm,
} from "@/lib/udfBuilder.mjs";
import { Card, Field, Notice, Select, Text, btnCls, inputCls, primaryCls, udfCall } from "./udfParts";

const opts = (envelope, kind, keys) => (Array.isArray(keys) ? keys : []).map((k) => ({ value: k, label: label(envelope, kind, k) }));

export function DefinitionForm({ envelope, original, onSaved, onCancel }) {
  const creating = !original;
  const [form, setForm] = useState(() => (original ? {
    ...original, type_params: { ...(original.type_params || {}) },
    default_value: original.default_value == null ? "" : String(original.default_value),
  } : { applies_to: "entity", data_type: "text", label: "", field_key: "", type_params: { length: 255 },
    is_required: false, help_text: "", description: "", default_value: "", display_order: 0 }));
  const [errors, setErrors] = useState({});
  const busy = useBusy();
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setParam = (k, v) => setForm((f) => ({ ...f, type_params: { ...f.type_params, [k]: v } }));
  const editable = new Set(Array.isArray(envelope?.vocabularies?.editable) ? envelope.vocabularies.editable : []);
  const rules = paramRules(envelope, form.data_type);
  const valueSets = Array.isArray(envelope?.value_sets) ? envelope.value_sets : [];
  const chosenSet = valueSets.find((v) => v.id === form.type_params?.value_set_id);
  const idp = creating ? "udf-new" : `udf-${original.id}`;

  async function save(e) {
    e.preventDefault();
    const errs = validateDefinitionForm(form, envelope, { creating });
    setErrors(errs);
    if (Object.keys(errs).length) return;
    await busy.run(async () => {
      const out = creating
        ? await udfCall("createDefinition", {}, { body: definitionCreateBody(form, envelope) })
        : await udfCall("editDefinition", { id: original.id }, { body: definitionPatchBody(original, form, envelope) });
      onSaved?.(out);
    });
  }

  const paramInput = (p, required) => {
    const id = `${idp}-tp-${p}`;
    const err = errors[`type_params.${p}`];
    const lbl = `${label(envelope, "type_param", p)}${required ? " (required)" : ""}`;
    const lock = !creating && !editable.has("type_params");
    if (p === "value_set_id") {
      return (
        <div key={p}>
          <Select id={id} label={lbl} value={form.type_params?.value_set_id} error={err} disabled={lock}
            placeholder="Choose a list" onChange={(v) => setParam(p, v)}
            options={valueSets.map((v) => ({ value: v.id, label: v.label || v.list_key }))}
            hint={valueSets.length ? undefined : "No picklist value lists exist for this firm yet."} />
          {chosenSet ? (
            <p className="-mt-2 mb-3 text-xs text-text-muted">
              Values: {(chosenSet.items || []).map((i) => i.label || i.code).join(", ") || "none"}
            </p>
          ) : null}
        </div>
      );
    }
    return <Text key={p} id={id} label={lbl} value={form.type_params?.[p]} error={err} disabled={lock}
      onChange={(v) => setParam(p, v)} />;
  };

  return (
    <form onSubmit={save} noValidate aria-label={creating ? "New custom field" : `Edit ${original.label}`}
      className="rounded-md border border-[#ece8dd] bg-[#FAF9F6] p-4">
      <div className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
        <Text id={`${idp}-label`} label="Name" value={form.label} error={errors.label}
          onChange={(v) => { set("label", v); if (creating && !form.__keyTouched) set("field_key", slugKey(v)); }} />
        {creating ? (
          <Text id={`${idp}-key`} label="Key" value={form.field_key} error={errors.field_key}
            hint="Stored with every value; cannot be changed later."
            onChange={(v) => { set("field_key", v); set("__keyTouched", true); }} />
        ) : <Text id={`${idp}-key`} label="Key" value={original.field_key} disabled onChange={() => {}} />}
        <Select id={`${idp}-target`} label="Record type" value={form.applies_to} error={errors.applies_to}
          disabled={!creating} onChange={(v) => set("applies_to", v)}
          options={opts(envelope, "applies_to", envelope?.vocabularies?.applies_to)} />
        <Select id={`${idp}-type`} label="Field type" value={form.data_type} error={errors.data_type}
          disabled={!creating} onChange={(v) => setForm((f) => ({ ...f, data_type: v, type_params: {} }))}
          options={opts(envelope, "data_type", envelope?.vocabularies?.data_type)} />
        {rules.required.map((p) => paramInput(p, true))}
        {rules.optional.map((p) => paramInput(p, false))}
        <Text id={`${idp}-help`} label="Help text" value={form.help_text} onChange={(v) => set("help_text", v)} />
        <Text id={`${idp}-desc`} label="Description" value={form.description} onChange={(v) => set("description", v)} />
        <Text id={`${idp}-default`} label="Default value" value={form.default_value}
          onChange={(v) => set("default_value", v)} />
        <Text id={`${idp}-order`} label="Display order" type="number" value={form.display_order}
          onChange={(v) => set("display_order", v)} />
        <Field id={`${idp}-req`} label="Required">
          {(a) => (
            <input {...a} type="checkbox" className="mt-2" checked={Boolean(form.is_required)}
              onChange={(e) => set("is_required", e.target.checked)} />
          )}
        </Field>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <BusyButton type="submit" busy={busy.busy} className={primaryCls}>{creating ? "Create field" : "Save"}</BusyButton>
        <button type="button" className={btnCls} onClick={onCancel}>Cancel</button>
        <BusyError error={busy.error} />
      </div>
    </form>
  );
}

function RowActions({ row, envelope, onChanged, onEdit }) {
  const busy = useBusy();
  const actions = definitionActions(row, envelope);
  if (!actions.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {actions.map((a) => (a === "editDefinition" ? (
        <button key={a} type="button" className={btnCls} data-action={a} onClick={() => onEdit(row)}>{ACTION_LABELS[a]}</button>
      ) : (
        <BusyButton key={a} className={btnCls} busy={busy.busy} data-action={a}
          aria-label={`${ACTION_LABELS[a]} ${row.label}`}
          onClick={() => busy.run(async () => { await udfCall(a, { id: row.id }); await onChanged?.(); })}>
          {ACTION_LABELS[a]}
        </BusyButton>
      )))}
      <BusyError error={busy.error} />
    </div>
  );
}

export default function FieldsPanel({ envelope, onChanged }) {
  const [filters, setFilters] = useState({ applies_to: "", status: "", data_type: "", q: "" });
  const [editing, setEditing] = useState(null); // null | "new" | row
  const write = canWrite(envelope);
  const rows = filterDefinitions(envelope?.rows, filters);
  const setF = (k, v) => setFilters((f) => ({ ...f, [k]: v }));
  const done = async () => { setEditing(null); await onChanged?.(); };

  const columnDefs = [
    { field: "label", headerName: "Name", cell: (v, r) => <span title={r.field_key}>{v}</span> },
    { field: "data_type", headerName: "Type", cell: (v) => label(envelope, "data_type", v) },
    { field: "applies_to", headerName: "Record type", cell: (v) => label(envelope, "applies_to", v) },
    { field: "owner_scope", headerName: "Scope", cell: (v) => label(envelope, "owner_scope", v) },
    { field: "placements", headerName: "Tab / section", enableSorting: false, cell: (_v, r) => placementText(r) },
    { field: "status", headerName: "Status", cell: (v) => label(envelope, "status", v) },
    { field: "used_by", headerName: "Used by", align: "right" },
    ...(write ? [{ field: "id", headerName: "Actions", enableSorting: false, enableColumnFilter: false,
      cell: (_v, r) => <RowActions row={r} envelope={envelope} onChanged={onChanged} onEdit={setEditing} /> }] : []),
  ];

  return (
    <Card title="Fields" actions={write && editing === null ? (
      <button type="button" className={primaryCls} data-action="createDefinition" onClick={() => setEditing("new")}>New field</button>
    ) : null}>
      {!write ? <Notice>You can see these fields but not change them.</Notice> : null}
      <div className="mb-3 grid grid-cols-1 gap-x-4 md:grid-cols-4" role="search" aria-label="Filter fields">
        <Select id="udf-f-target" label="Record type" value={filters.applies_to} placeholder="All"
          onChange={(v) => setF("applies_to", v)} options={opts(envelope, "applies_to", envelope?.vocabularies?.applies_to)} />
        <Select id="udf-f-status" label="Status" value={filters.status} placeholder="All"
          onChange={(v) => setF("status", v)} options={opts(envelope, "status", envelope?.vocabularies?.statuses)} />
        <Select id="udf-f-type" label="Field type" value={filters.data_type} placeholder="All"
          onChange={(v) => setF("data_type", v)} options={opts(envelope, "data_type", envelope?.vocabularies?.data_type)} />
        <Field id="udf-f-q" label="Search">
          {(a) => <input {...a} type="search" className={inputCls} value={filters.q} onChange={(e) => setF("q", e.target.value)} />}
        </Field>
      </div>
      {write && editing ? (
        <div className="mb-4">
          <DefinitionForm key={editing === "new" ? "new" : editing.id} envelope={envelope}
            original={editing === "new" ? null : editing} onSaved={done} onCancel={() => setEditing(null)} />
        </div>
      ) : null}
      <DataGrid gridId="udf-fields" columnDefs={columnDefs} rowData={rows} getRowId={(r) => r.id} />
    </Card>
  );
}
