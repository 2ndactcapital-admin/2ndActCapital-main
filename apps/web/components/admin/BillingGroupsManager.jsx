"use client";

/**
 * BillingGroupsManager — the manage-billing-groups screen (sprint fee33).
 *
 * WHAT THE SERVER DECIDES AND THIS FILE ONLY RENDERS
 * ─────────────────────────────────────────────────────────────────────────
 *   · `permissions.can_write` — whether ANY write control exists. No local
 *     default, no `?? true`, no `|| DEFAULTS`: a missing envelope fails CLOSED.
 *     It is not the enforcement either; every endpoint re-checks, and
 *     verify_fee33 asserts the two independently, because a hidden control over
 *     an open endpoint and a gated endpoint under a visible button are both
 *     real bugs and neither is ruled out by testing the other.
 *   · `vocabularies.group_type` — the type list. Never a hardcoded array here;
 *     fee34 widens it and this screen must follow without an edit.
 *   · `vocabularies.exclusive_group_types` — WHICH types restrict membership.
 *     The explanatory copy is built from this rather than saying "BREAKPOINT"
 *     in a string, so the screen cannot keep explaining a rule that moved.
 *   · `candidates[].blocking_group_name` — why an account cannot be added.
 *     Computed server-side by the same query the constraint uses, so the greyed
 *     row and the 409 can never disagree.
 *   · every 409 message — surfaced verbatim, never re-derived.
 *
 * WHY BLOCKED ACCOUNTS ARE SHOWN RATHER THAN FILTERED OUT
 * ─────────────────────────────────────────────────────────────────────────
 * An account missing from the picker looks like a data problem and sends the
 * operator to go looking for it. An account visible but greyed, captioned "in
 * the Henderson Family Breakpoint group", answers the question in place.
 *
 * Cream/white only, hairline borders, no shadows beyond the shared card
 * treatment — every colour goes through the `--2a-*` tokens the layout injects
 * from org_settings. No Signature-palette hex is written here.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

const TYPE_BLURB = {
  BREAKPOINT: "Values sum together to decide the fee tier.",
  STATEMENT: "Accounts that print on one statement.",
  PAYER: "Accounts settled by a single payer.",
};

export default function BillingGroupsManager({
  initialRows = [],
  initialPermissions = null,
  initialVocabularies = null,
  households = [],
}) {
  const [rows, setRows] = useState(initialRows);
  // NO FALLBACK. `can_write` is false unless the server said otherwise.
  const [permissions, setPermissions] = useState(
    initialPermissions || { can_read: true, can_write: false },
  );
  const [vocabularies, setVocabularies] = useState(
    initialVocabularies || {
      group_type: [],
      exclusive_group_types: [],
      editable: [],
      inline_editable: [],
    },
  );
  const [selectedId, setSelectedId] = useState(null);
  const [members, setMembers] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const canWrite = !!permissions?.can_write;
  const groupTypes = vocabularies?.group_type || [];
  const exclusiveTypes = vocabularies?.exclusive_group_types || [];

  const selected = useMemo(
    () => rows.find((r) => String(r.id) === String(selectedId)) || null,
    [rows, selectedId],
  );

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/billing-groups", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(typeof data.error === "string" ? data.error : "Could not load groups.");
        return;
      }
      setRows(data.rows || []);
      if (data.permissions) setPermissions(data.permissions);
      if (data.vocabularies) setVocabularies(data.vocabularies);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const loadMembers = useCallback(async (groupId) => {
    if (!groupId) {
      setMembers([]);
      setCandidates([]);
      return;
    }
    try {
      const res = await fetch(`/api/billing-groups/${groupId}/members`, {
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError("Could not load members.");
        return;
      }
      setMembers(data.rows || []);
      setCandidates(data.candidates || []);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    loadMembers(selectedId);
  }, [selectedId, loadMembers]);

  // A selected group that has just been archived must not leave the pane
  // rendering a stale copy.
  useEffect(() => {
    if (selectedId && !selected) setSelectedId(null);
  }, [selectedId, selected]);

  /** Surface the server's own message. A 409 carries both group names. */
  async function mutate(url, options, successMessage) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(url, { cache: "no-store", ...options });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data?.detail;
        setError(
          (detail && typeof detail === "object" && detail.message) ||
            (typeof detail === "string" && detail) ||
            data?.error ||
            "The change was refused.",
        );
        return false;
      }
      setNotice(successMessage);
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const columnDefs = useMemo(
    () => [
      {
        field: "name",
        headerName: "Group",
        enableColumnFilter: true,
        filterPlaceholder: "Name…",
        cell: (value) => (
          <span className="font-medium text-[var(--2a-navy)]">{value}</span>
        ),
      },
      {
        field: "group_type",
        headerName: "Type",
        enableColumnFilter: true,
        filterPlaceholder: "Type…",
        cell: (value) => (
          <span className="text-[var(--2a-text)]">
            {value}
            {exclusiveTypes.includes(value) ? (
              <span className="ml-2 text-xs text-[var(--2a-text-muted)]">
                one per account
              </span>
            ) : null}
          </span>
        ),
      },
      {
        field: "household_name",
        headerName: "Household",
        cell: (value) =>
          value ? (
            <span className="text-[var(--2a-text)]">{value}</span>
          ) : (
            // NULL is a first-class state, not a gap to nag about.
            <span className="text-[var(--2a-text-muted)]">Not linked</span>
          ),
      },
      { field: "member_count", headerName: "Accounts", align: "right" },
      {
        field: "notes",
        headerName: "Notes",
        cell: (value) => (
          <span className="text-[var(--2a-text-muted)]">{value || "—"}</span>
        ),
      },
    ],
    [exclusiveTypes],
  );

  return (
    <div className="mt-6 space-y-6">
      {error ? (
        <div
          className="rounded border bg-bg-card p-4 text-sm text-[var(--2a-error)]"
          style={CARD}
          role="alert"
        >
          {error}
        </div>
      ) : null}
      {notice ? (
        <div
          className="rounded border bg-bg-card p-4 text-sm text-[var(--2a-success)]"
          style={CARD}
        >
          {notice}
        </div>
      ) : null}

      <div className="rounded border bg-bg-card p-5" style={CARD}>
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-medium text-[var(--2a-navy)]">Groups</h2>
          {/* The ONLY gate on the create control. No truthy fallback. */}
          {canWrite ? (
            <button
              type="button"
              onClick={() => setCreating((v) => !v)}
              className="text-sm text-[var(--2a-gold)] hover:underline"
            >
              {creating ? "Cancel" : "New group"}
            </button>
          ) : null}
        </div>

        {canWrite && creating ? (
          <CreateGroupForm
            groupTypes={groupTypes}
            households={households}
            busy={busy}
            onSubmit={async (body) => {
              const ok = await mutate(
                "/api/billing-groups",
                {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(body),
                },
                `Created ${body.name}.`,
              );
              if (ok) {
                setCreating(false);
                await reload();
              }
            }}
          />
        ) : null}

        <div className="mt-4">
          <DataGrid
            gridId="billing-groups"
            columnDefs={columnDefs}
            rowData={rows}
            onRowClick={(row) => setSelectedId(row.id)}
            quickFilterPlaceholder="Search groups…"
            emptyMessage="No billing groups yet."
          />
        </div>
      </div>

      {selected ? (
        <MemberPane
          group={selected}
          members={members}
          candidates={candidates}
          canWrite={canWrite}
          busy={busy}
          isExclusive={exclusiveTypes.includes(selected.group_type)}
          onAdd={async (accountId) => {
            const ok = await mutate(
              `/api/billing-groups/${selected.id}/members`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ account_id: accountId }),
              },
              "Account added.",
            );
            if (ok) {
              await loadMembers(selected.id);
              await reload();
            }
          }}
          onRemove={async (accountId) => {
            const ok = await mutate(
              `/api/billing-groups/${selected.id}/members/${accountId}`,
              { method: "DELETE" },
              "Account removed.",
            );
            if (ok) {
              await loadMembers(selected.id);
              await reload();
            }
          }}
          onArchive={async () => {
            const ok = await mutate(
              `/api/billing-groups/${selected.id}`,
              { method: "DELETE" },
              `Archived ${selected.name}.`,
            );
            if (ok) {
              setSelectedId(null);
              await reload();
            }
          }}
        />
      ) : null}
    </div>
  );
}

function CreateGroupForm({ groupTypes, households, busy, onSubmit }) {
  const [name, setName] = useState("");
  const [groupType, setGroupType] = useState(groupTypes[0] || "");
  const [householdId, setHouseholdId] = useState("");
  const [notes, setNotes] = useState("");

  return (
    <form
      className="mt-4 grid gap-4 border-t pt-4 sm:grid-cols-2"
      style={{ borderColor: "#ece8dd" }}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          name: name.trim(),
          group_type: groupType,
          // "" means the operator left it blank, which is NULL — not the empty
          // string, which the backend would reject as a malformed uuid.
          household_id: householdId || null,
          notes: notes.trim() || null,
        });
      }}
    >
      <label className="text-sm">
        <span className="block text-[var(--2a-text-muted)]">Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          className="mt-1 w-full rounded border px-3 py-2"
          style={{ borderColor: "#ece8dd" }}
        />
      </label>

      <label className="text-sm">
        <span className="block text-[var(--2a-text-muted)]">Type</span>
        <select
          value={groupType}
          onChange={(e) => setGroupType(e.target.value)}
          className="mt-1 w-full rounded border bg-white px-3 py-2"
          style={{ borderColor: "#ece8dd" }}
        >
          {/* From the server's vocabulary, never a literal array here. */}
          {groupTypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="mt-1 block text-xs text-[var(--2a-text-muted)]">
          {TYPE_BLURB[groupType] || ""}
        </span>
      </label>

      <label className="text-sm">
        <span className="block text-[var(--2a-text-muted)]">
          Household (optional)
        </span>
        <select
          value={householdId}
          onChange={(e) => setHouseholdId(e.target.value)}
          className="mt-1 w-full rounded border bg-white px-3 py-2"
          style={{ borderColor: "#ece8dd" }}
        >
          {/* Leading and default, because a group belongs to no household as
              often as it belongs to one — a billing group is not a household. */}
          <option value="">Not linked to a household</option>
          {households.map((h) => (
            <option key={h.id} value={h.id}>
              {h.name}
            </option>
          ))}
        </select>
        <span className="mt-1 block text-xs text-[var(--2a-text-muted)]">
          A label for filing only — membership is the accounts you add below.
        </span>
      </label>

      <label className="text-sm">
        <span className="block text-[var(--2a-text-muted)]">Notes</span>
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="mt-1 w-full rounded border px-3 py-2"
          style={{ borderColor: "#ece8dd" }}
        />
      </label>

      <div className="sm:col-span-2">
        <button
          type="submit"
          disabled={busy || !name.trim() || !groupType}
          className="rounded bg-[var(--2a-navy)] px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          Create group
        </button>
      </div>
    </form>
  );
}

function MemberPane({
  group,
  members,
  candidates,
  canWrite,
  busy,
  isExclusive,
  onAdd,
  onRemove,
  onArchive,
}) {
  const [pick, setPick] = useState("");

  const memberColumns = useMemo(() => {
    const cols = [
      { field: "account_number_masked", headerName: "Account" },
      { field: "custodian_code", headerName: "Custodian" },
      { field: "registration_type", headerName: "Registration" },
      {
        field: "is_billable",
        headerName: "Billable",
        cell: (v) => (v ? "Yes" : "No"),
      },
    ];
    // The remove control exists only inside the can_write test — the column
    // itself is absent for a view-only caller, not merely disabled.
    if (canWrite) {
      cols.push({
        field: "account_id",
        headerName: "",
        align: "right",
        cell: (value) => (
          <button
            type="button"
            disabled={busy}
            onClick={() => onRemove(value)}
            className="text-sm text-[var(--2a-gold)] hover:underline disabled:opacity-50"
          >
            Remove
          </button>
        ),
      });
    }
    return cols;
  }, [canWrite, busy, onRemove]);

  const addable = candidates.filter((c) => !c.already_in_group);

  return (
    <div className="rounded border bg-bg-card p-5" style={CARD}>
      <div className="flex items-baseline justify-between">
        <div>
          <h2 className="text-lg font-medium text-[var(--2a-navy)]">
            {group.name}
          </h2>
          <p className="mt-1 text-sm text-[var(--2a-text-muted)]">
            {group.group_type}
            {isExclusive
              ? " — an account may belong to only one group of this type at a time"
              : " — an account may belong to several groups of this type"}
          </p>
        </div>
        {canWrite ? (
          <button
            type="button"
            disabled={busy}
            onClick={onArchive}
            className="text-sm text-[var(--2a-gold)] hover:underline disabled:opacity-50"
          >
            Archive group
          </button>
        ) : null}
      </div>

      {canWrite ? (
        <div
          className="mt-4 flex flex-wrap items-end gap-3 border-t pt-4"
          style={{ borderColor: "#ece8dd" }}
        >
          <label className="text-sm">
            <span className="block text-[var(--2a-text-muted)]">Add account</span>
            <select
              value={pick}
              onChange={(e) => setPick(e.target.value)}
              className="mt-1 min-w-[22rem] rounded border bg-white px-3 py-2"
              style={{ borderColor: "#ece8dd" }}
            >
              <option value="">Select an account…</option>
              {addable.map((c) => (
                <option
                  key={c.id}
                  value={c.id}
                  // Blocked accounts stay VISIBLE and disabled, captioned with
                  // the group that holds them. An account merely missing from
                  // the list reads as a data problem.
                  disabled={!!c.blocking_group_id}
                >
                  {c.account_number_masked}
                  {c.blocking_group_id
                    ? ` — already in ${c.blocking_group_name}`
                    : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || !pick}
            onClick={async () => {
              await onAdd(pick);
              setPick("");
            }}
            className="rounded bg-[var(--2a-navy)] px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            Add
          </button>
        </div>
      ) : null}

      <div className="mt-4">
        <DataGrid
          gridId="billing-group-members"
          columnDefs={memberColumns}
          rowData={members}
          enablePagination={false}
          enableGlobalFilter={false}
          emptyMessage="No accounts in this group yet."
        />
      </div>
    </div>
  );
}
