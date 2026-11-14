"use client";

// UDFUI1 — Tabs: every custom tab of the firm (any status) by record type; create, rename, order (display_order via
// the tab PATCH), deactivate / reactivate, delete (refused by the engine while the layout is non-empty — its reason is
// shown) / undelete; choosing a tab opens its layout editor. Write controls only when permissions.can_write.
import { useState } from "react";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { ACTION_LABELS, canWrite, label, moveInList, slugKey, tabActions, tabOrderPatches } from "@/lib/udfBuilder.mjs";
import LayoutEditor from "./LayoutEditor";
import { Card, Notice, Select, Text, btnCls, primaryCls, udfCall } from "./udfParts";

function TabRow({ tab, index, count, envelope, onMove, onChanged, onOpen, open }) {
  const busy = useBusy();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(tab.label);
  const actions = tabActions(tab, envelope);
  const write = canWrite(envelope);
  return (
    <li className="flex flex-wrap items-center gap-2 border-b border-border py-2" data-tab-id={tab.id}>
      {write && tab.status !== "deleted" ? (
        <span className="flex gap-1">
          <button type="button" className={btnCls} aria-label={`Move ${tab.label} up`} disabled={index === 0}
            onClick={() => onMove(index, -1)}>↑</button>
          <button type="button" className={btnCls} aria-label={`Move ${tab.label} down`} disabled={index === count - 1}
            onClick={() => onMove(index, 1)}>↓</button>
        </span>
      ) : null}
      {renaming ? (
        <form className="flex items-end gap-2" onSubmit={(e) => {
          e.preventDefault();
          busy.run(async () => { await udfCall("editTab", { id: tab.id }, { body: { label: name } }); setRenaming(false); await onChanged(); });
        }}>
          <Text id={`udf-tab-name-${tab.id}`} label="Tab name" value={name} onChange={setName} />
          <BusyButton type="submit" busy={busy.busy} className={primaryCls}>Save</BusyButton>
          <button type="button" className={btnCls} onClick={() => setRenaming(false)}>Cancel</button>
        </form>
      ) : (
        <button type="button" className="text-left font-medium text-navy hover:underline" aria-expanded={open}
          onClick={() => onOpen(tab)}>{tab.label}</button>
      )}
      <span className="text-xs text-text-muted">{label(envelope, "status", tab.status)} · {tab.section_count} section(s), {tab.item_count} field(s)</span>
      <span className="ml-auto flex flex-wrap gap-1">
        {actions.map((a) => (a === "editTab" ? (
          <button key={a} type="button" className={btnCls} data-action={a} onClick={() => setRenaming(true)}>{ACTION_LABELS[a]}</button>
        ) : (
          <BusyButton key={a} className={btnCls} busy={busy.busy} data-action={a} aria-label={`${ACTION_LABELS[a]} ${tab.label}`}
            onClick={() => busy.run(async () => { await udfCall(a, { id: tab.id }); await onChanged(); })}>{ACTION_LABELS[a]}</BusyButton>
        )))}
      </span>
      <BusyError error={busy.error} />
    </li>
  );
}

export default function TabsPanel({ envelope, definitions, onChanged }) {
  const write = canWrite(envelope);
  const targets = Array.isArray(envelope?.vocabularies?.applies_to) ? envelope.vocabularies.applies_to : [];
  const [target, setTarget] = useState(targets.includes("entity") ? "entity" : targets[0] || "");
  const [openId, setOpenId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ label: "", api_name: "" });
  const [errors, setErrors] = useState({});
  const busy = useBusy();
  const rows = (Array.isArray(envelope?.rows) ? envelope.rows : []).filter((t) => t.applies_to === target);
  const live = rows.filter((t) => t.status !== "deleted");
  const deleted = rows.filter((t) => t.status === "deleted");
  const openTab = rows.find((t) => t.id === openId) || null;

  const move = (index, delta) => busy.run(async () => {
    const ids = moveInList(live.map((t) => t.id), index, delta);
    for (const p of tabOrderPatches(live, ids)) await udfCall("editTab", { id: p.id }, { body: p.body });
    await onChanged();
  });

  const create = (e) => {
    e.preventDefault();
    const errs = {};
    if (!form.label.trim()) errs.label = "A name is required.";
    const api = form.api_name.trim() || slugKey(form.label);
    if (!api) errs.api_name = "A key is required.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    busy.run(async () => {
      await udfCall("createTab", {}, { body: { applies_to: target, label: form.label.trim(), api_name: api, display_order: live.length } });
      setCreating(false); setForm({ label: "", api_name: "" }); await onChanged();
    });
  };

  return (
    <Card title="Custom tabs" actions={write && !creating ? (
      <button type="button" className={primaryCls} data-action="createTab" onClick={() => setCreating(true)}>New tab</button>
    ) : null}>
      <div className="max-w-xs">
        <Select id="udf-tabs-target" label="Record type" value={target} onChange={(v) => { setTarget(v); setOpenId(null); }}
          options={targets.map((k) => ({ value: k, label: label(envelope, "applies_to", k) }))} />
      </div>
      {write && creating ? (
        <form onSubmit={create} noValidate aria-label="New custom tab" className="mb-3 rounded-md border border-[#ece8dd] bg-[#FAF9F6] p-3">
          <Text id="udf-tab-new-label" label="Tab name" value={form.label} error={errors.label}
            onChange={(v) => setForm((f) => ({ ...f, label: v }))} />
          <Text id="udf-tab-new-key" label="Key" value={form.api_name} error={errors.api_name}
            hint="Leave blank to derive it from the name; cannot be changed later."
            onChange={(v) => setForm((f) => ({ ...f, api_name: v }))} />
          <div className="flex items-center gap-2">
            <BusyButton type="submit" busy={busy.busy} className={primaryCls}>Create tab</BusyButton>
            <button type="button" className={btnCls} onClick={() => setCreating(false)}>Cancel</button>
          </div>
        </form>
      ) : null}
      <BusyError error={busy.error} />
      {rows.length === 0 ? <Notice>No custom tabs for this record type yet.</Notice> : null}
      <ul aria-label="Custom tabs">
        {[...live, ...deleted].map((t, i) => (
          <TabRow key={t.id} tab={t} index={i} count={live.length} envelope={envelope} onMove={move}
            onChanged={onChanged} open={openId === t.id} onOpen={(tab) => setOpenId(openId === tab.id ? null : tab.id)} />
        ))}
      </ul>
      {openTab && openTab.status !== "deleted" ? (
        <LayoutEditor key={openTab.id} tab={openTab} envelope={envelope} definitions={definitions} onChanged={onChanged} />
      ) : null}
    </Card>
  );
}
