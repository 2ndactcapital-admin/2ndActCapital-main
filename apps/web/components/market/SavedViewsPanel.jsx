"use client";

import { useEffect, useState } from "react";

import SavedViewsView from "@/components/market/SavedViewsView";
import { requestJson } from "@/components/market/marketClient.mjs";
import {
  EMPTY_SAVE,
  VIEWS_ROUTE,
  VIEW_DELETE_FAILED,
  VIEW_SAVE_FAILED,
  canSaveView,
  createViewRequest,
  deleteViewRequest,
  interpretViews,
  updateViewRequest,
  writeOutcome,
} from "@/lib/market/viewsModel.mjs";

/**
 * The saved views' state and requests (mkt04c Task 4). Views load once on
 * page entry through GET /api/market/views (fail closed:
 * lib/market/viewsModel.mjs interpretViews) and again after each write, so the
 * list changes only from the server's response. A failure here stays in this
 * card: the chart and the grid never wait for it.
 *
 * Props: currentConfig (buildViewConfig of the page's state), notices,
 * onLoadView(view).
 */
export default function SavedViewsPanel({ currentConfig, notices, onLoadView }) {
  const [state, setState] = useState({ kind: "loading" });
  const [version, setVersion] = useState(0);
  const [activeId, setActiveId] = useState(null);
  const [form, setForm] = useState(EMPTY_SAVE);
  const [pending, setPending] = useState(null);
  const [update, setUpdate] = useState({ busy: false, error: null });

  useEffect(() => {
    let alive = true;
    (async () => {
      const next = interpretViews(await requestJson(VIEWS_ROUTE));
      if (alive) setState(next);
    })();
    return () => {
      alive = false;
    };
  }, [version]);
  const reload = () => setVersion((v) => v + 1);

  const save = async () => {
    if (!canSaveView(form)) return;
    const sent = { ...form, saving: true, error: null };
    setForm(sent);
    const req = createViewRequest(form.name, currentConfig);
    const out = writeOutcome(await requestJson(req.url, { method: req.method, body: req.body }), VIEW_SAVE_FAILED);
    if (out.ok) {
      setForm(EMPTY_SAVE);
      if (typeof out.row?.id === "string") setActiveId(out.row.id);
      reload();
    } else {
      setForm({ ...sent, saving: false, error: out.message });
    }
  };

  const updateView = async (id) => {
    setUpdate({ busy: true, error: null });
    const req = updateViewRequest(id, currentConfig);
    const out = writeOutcome(await requestJson(req.url, { method: req.method, body: req.body }), VIEW_SAVE_FAILED);
    setUpdate({ busy: false, error: out.ok ? null : out.message });
    if (out.ok) reload();
  };

  const confirmDelete = async () => {
    if (!pending || pending.busy) return;
    const sent = { ...pending, busy: true, error: null };
    setPending(sent);
    const req = deleteViewRequest(sent.id);
    const out = writeOutcome(await requestJson(req.url, { method: req.method }), VIEW_DELETE_FAILED);
    if (out.ok) {
      setPending(null);
      if (activeId === sent.id) setActiveId(null);
      reload();
    } else {
      setPending({ ...sent, busy: false, error: out.message });
    }
  };

  return (
    <SavedViewsView
      state={state}
      activeId={activeId}
      notices={notices}
      form={form}
      pendingDelete={pending}
      updateError={update.error}
      updating={update.busy}
      onLoad={(view) => {
        setActiveId(view.id);
        setUpdate({ busy: false, error: null });
        onLoadView(view);
      }}
      onUpdate={updateView}
      onDeleteRequest={(id) => setPending({ id, busy: false, error: null })}
      onDeleteConfirm={confirmDelete}
      onDeleteCancel={() => setPending(null)}
      onNameChange={(name) => setForm((f) => ({ ...f, name }))}
      onSave={save}
    />
  );
}
