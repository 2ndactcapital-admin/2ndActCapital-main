"use client";

import { useState } from "react";

import KeyDatesBar from "@/components/market/KeyDatesBar";
import { requestJson } from "@/components/market/marketClient.mjs";
import {
  afterDelete,
  afterSubmit,
  canWriteDates,
  closeForm,
  confirmDelete,
  editForm,
  nameMaxLength,
  openForm,
  requestDelete,
  submitForm,
} from "@/lib/market/customDates.mjs";
import { SOURCE_MY, anchorDateFor, pickDate, resolveDateSelection, setEdge } from "@/lib/market/keyDatesModel.mjs";

/**
 * The key-dates bar's state and requests (mkt04c Task 3). Every step is a
 * lib/market/customDates.mjs or keyDatesModel.mjs function; this only holds
 * the form and the pending delete, sends what those functions build, and tells
 * the page what changed:
 *   onPickDate(sel, anchorIso)  a date was picked (anchor moves to its period),
 *                               or cleared (sel null, anchor unchanged);
 *   onKeyDatesChanged()         re-read the list from the server.
 * Nothing changes the list locally: a save or delete asks the server again.
 *
 * Props: keyDates, dateSel, periods (the chart's), onPickDate, onKeyDatesChanged.
 */
export default function KeyDatesPanel({ keyDates, dateSel, periods, onPickDate, onKeyDatesChanged }) {
  const [form, setForm] = useState(closeForm);
  const [pending, setPending] = useState(null);
  const resolved = resolveDateSelection(dateSel ?? null, keyDates);

  const pickTo = (sel, iso) => onPickDate?.(sel, sel && iso ? anchorDateFor(periods, iso) : null);
  const pick = (source, id) => {
    const sel = pickDate(source, id);
    setPending(null);
    pickTo(sel, resolveDateSelection(sel, keyDates)?.target ?? null);
  };
  const edge = (e) => {
    const sel = setEdge(dateSel, e);
    pickTo(sel, resolveDateSelection(sel, keyDates)?.target ?? null);
  };

  const save = async () => {
    const step = submitForm(form);
    if (!step) return;
    setForm(step.form);
    const res = await requestJson(step.request.url, { method: step.request.method, body: step.request.body });
    const out = afterSubmit(step.form, res);
    setForm(out.form);
    if (out.created) {
      pickTo(pickDate(SOURCE_MY, out.created.id), out.created.event_date);
      onKeyDatesChanged?.();
    }
  };

  const remove = async () => {
    const step = confirmDelete(pending);
    if (!step) return;
    setPending(step.pending);
    const res = await requestJson(step.request.url, { method: step.request.method });
    const out = afterDelete(step.pending, res);
    setPending(out.pending);
    if (out.deleted) {
      onPickDate?.(null, null);
      onKeyDatesChanged?.();
    }
  };

  return (
    <KeyDatesBar
      keyDates={keyDates}
      dateSel={dateSel ?? null}
      resolved={resolved}
      canWrite={canWriteDates(keyDates)}
      nameMax={keyDates?.kind === "ready" ? nameMaxLength(keyDates.limits) : undefined}
      form={form}
      pendingDelete={pending}
      onPick={pick}
      onEdge={edge}
      onOpenForm={() => setForm(openForm())}
      onFormEdit={(field, value) => setForm((f) => editForm(f, field, value))}
      onFormSave={save}
      onFormCancel={() => setForm(closeForm())}
      onDeleteRequest={(id) => setPending(requestDelete(id))}
      onDeleteConfirm={remove}
      onDeleteCancel={() => setPending(null)}
    />
  );
}
