"use client";

import { useState, useTransition } from "react";
import {
  grantAccessAction,
  revokeAccessAction,
  setRestrictedAction,
} from "@/lib/restrictedAccessActions";

const CARD = {
  borderColor: "#ece8dd",
  boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
};

function userLabel(u) {
  return u.full_name || u.email || u.id;
}

function entityLabel(e) {
  return e.display_name || e.name || e.id;
}

function Card({ title, hint, children }) {
  return (
    <section className="rounded-lg border bg-bg-card p-5" style={CARD}>
      <h2 className="text-base font-semibold text-navy">{title}</h2>
      {hint && <p className="mt-1 text-sm text-text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function inputClass() {
  return "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
}

export default function RestrictedAccessManager({
  initialRestricted = [],
  users = [],
  entities = [],
}) {
  const [restricted, setRestricted] = useState(initialRestricted);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  // Flag-an-entity form
  const [flagEntityId, setFlagEntityId] = useState("");
  const [flagNotes, setFlagNotes] = useState("");

  // Grant-access form (per restricted entity)
  const [grantEntityId, setGrantEntityId] = useState("");
  const [grantUserId, setGrantUserId] = useState("");
  const [grantReason, setGrantReason] = useState("");

  const restrictedIds = new Set(restricted.map((r) => r.id));

  function run(fn) {
    setError(null);
    startTransition(async () => {
      const res = await fn();
      if (!res.ok) setError(res.error || "Request failed.");
      return res;
    });
  }

  function flagEntity() {
    if (!flagEntityId) {
      setError("Pick an entity to restrict.");
      return;
    }
    run(async () => {
      const res = await setRestrictedAction(flagEntityId, true, flagNotes.trim());
      if (res.ok) {
        setRestricted(res.restricted);
        setFlagEntityId("");
        setFlagNotes("");
      }
      return res;
    });
  }

  function unflagEntity(entityId) {
    run(async () => {
      const res = await setRestrictedAction(entityId, false, null);
      if (res.ok) setRestricted(res.restricted);
      return res;
    });
  }

  function submitGrant() {
    if (!grantEntityId || !grantUserId) {
      setError("Pick a restricted account and a member.");
      return;
    }
    run(async () => {
      const res = await grantAccessAction(
        grantEntityId,
        grantUserId,
        grantReason.trim()
      );
      if (res.ok) {
        setRestricted(res.restricted);
        setGrantUserId("");
        setGrantReason("");
      }
      return res;
    });
  }

  function revoke(entityId, userId) {
    run(async () => {
      const res = await revokeAccessAction(entityId, userId);
      if (res.ok) setRestricted(res.restricted);
      return res;
    });
  }

  // Entities not already restricted, for the flag selector.
  const flaggable = entities.filter((e) => !restrictedIds.has(e.id));

  return (
    <div className="mt-6 space-y-6">
      {error && (
        <div className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm text-[#9B2335]">
          {error}
        </div>
      )}
      {pending && <p className="text-xs text-text-muted">Working…</p>}

      {/* Flag an entity */}
      <Card
        title="Restrict an account"
        hint="A restricted account is hidden from search and list results for anyone not on its allow-list."
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Account
            </label>
            <select
              className={`mt-1 w-72 ${inputClass()}`}
              value={flagEntityId}
              onChange={(e) => setFlagEntityId(e.target.value)}
            >
              <option value="">Select an account…</option>
              {flaggable.map((e) => (
                <option key={e.id} value={e.id}>
                  {entityLabel(e)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Notes
            </label>
            <input
              className={`mt-1 w-72 ${inputClass()}`}
              value={flagNotes}
              onChange={(e) => setFlagNotes(e.target.value)}
              placeholder="Optional — reason for restriction"
            />
          </div>
          <button
            type="button"
            onClick={flagEntity}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Restrict
          </button>
        </div>
      </Card>

      {/* Grant access */}
      <Card
        title="Grant access"
        hint="Add a member to a restricted account's allow-list so they can see it."
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Restricted account
            </label>
            <select
              className={`mt-1 w-64 ${inputClass()}`}
              value={grantEntityId}
              onChange={(e) => setGrantEntityId(e.target.value)}
            >
              <option value="">Select…</option>
              {restricted.map((r) => (
                <option key={r.id} value={r.id}>
                  {entityLabel(r)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Member
            </label>
            <select
              className={`mt-1 w-64 ${inputClass()}`}
              value={grantUserId}
              onChange={(e) => setGrantUserId(e.target.value)}
            >
              <option value="">Select…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {userLabel(u)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Reason
            </label>
            <input
              className={`mt-1 w-64 ${inputClass()}`}
              value={grantReason}
              onChange={(e) => setGrantReason(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <button
            type="button"
            onClick={submitGrant}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Grant access
          </button>
        </div>
      </Card>

      {/* Restricted accounts + allow-lists */}
      <Card
        title="Restricted accounts"
        hint="Each account and who may currently see it."
      >
        {restricted.length === 0 ? (
          <p className="text-sm text-text-muted">No restricted accounts.</p>
        ) : (
          <ul className="space-y-3">
            {restricted.map((r) => (
              <li
                key={r.id}
                className="rounded-md border border-border bg-bg-app p-3"
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-text-primary">
                    {entityLabel(r)}
                    <span className="ml-2 text-xs text-text-muted">
                      {r.entity_type}
                    </span>
                  </p>
                  <button
                    type="button"
                    onClick={() => unflagEntity(r.id)}
                    disabled={pending}
                    className="rounded-md border border-border px-3 py-1 text-xs text-text-primary hover:bg-bg-card disabled:opacity-60"
                  >
                    Unrestrict
                  </button>
                </div>
                <div className="mt-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
                    Allow-list
                  </p>
                  {(!r.grants || r.grants.length === 0) ? (
                    <p className="mt-1 text-xs text-text-muted">
                      No one — visible only to Super Admins.
                    </p>
                  ) : (
                    <ul className="mt-1 space-y-1">
                      {r.grants.map((g) => (
                        <li
                          key={g.user_id}
                          className="flex items-center justify-between text-sm text-text-primary"
                        >
                          <span>
                            {g.full_name || g.email || g.user_id}
                            {g.reason && (
                              <span className="ml-2 text-xs text-text-muted">
                                {g.reason}
                              </span>
                            )}
                          </span>
                          <button
                            type="button"
                            onClick={() => revoke(r.id, g.user_id)}
                            disabled={pending}
                            className="text-xs text-[#9B2335] hover:underline disabled:opacity-60"
                          >
                            Revoke
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
