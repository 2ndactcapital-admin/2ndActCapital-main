"use client";

import { useState, useTransition } from "react";
import {
  addMemberAction,
  createAssignmentAction,
  createTeamAction,
  deleteAssignmentAction,
  removeMemberAction,
} from "@/lib/staffAssignmentActions";

const CARD = {
  borderColor: "#ece8dd",
  boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
};

function userLabel(u) {
  return u.full_name || u.email || u.id;
}

function Card({ title, hint, children }) {
  return (
    <section
      className="rounded-lg border bg-bg-card p-5"
      style={CARD}
    >
      <h2 className="text-base font-semibold text-navy">{title}</h2>
      {hint && <p className="mt-1 text-sm text-text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function inputClass() {
  return "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
}

export default function StaffVisibilityManager({
  initialTeams = [],
  initialAssignments = [],
  users = [],
  entities = [],
}) {
  const [teams, setTeams] = useState(initialTeams);
  const [assignments, setAssignments] = useState(initialAssignments);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  // Create-team form
  const [teamName, setTeamName] = useState("");
  const [teamDesc, setTeamDesc] = useState("");

  // Add-member form (per selected team)
  const [memberTeamId, setMemberTeamId] = useState("");
  const [memberUserId, setMemberUserId] = useState("");

  // Create-assignment form
  const [entityId, setEntityId] = useState("");
  const [targetType, setTargetType] = useState("user");
  const [targetId, setTargetId] = useState("");
  const [roleLabel, setRoleLabel] = useState("");

  function run(fn) {
    setError(null);
    startTransition(async () => {
      const res = await fn();
      if (!res.ok) setError(res.error || "Request failed.");
      return res;
    });
  }

  function submitTeam() {
    if (!teamName.trim()) {
      setError("Team name is required.");
      return;
    }
    run(async () => {
      const res = await createTeamAction(teamName.trim(), teamDesc.trim());
      if (res.ok) {
        setTeams((prev) => [...prev, { ...res.team, members: [] }]);
        setTeamName("");
        setTeamDesc("");
      }
      return res;
    });
  }

  function submitMember() {
    if (!memberTeamId || !memberUserId) {
      setError("Pick a team and a user.");
      return;
    }
    run(async () => {
      const res = await addMemberAction(memberTeamId, memberUserId);
      if (res.ok) {
        setTeams(res.teams);
        setMemberUserId("");
      }
      return res;
    });
  }

  function removeMember(teamId, userId) {
    run(async () => {
      const res = await removeMemberAction(teamId, userId);
      if (res.ok) setTeams(res.teams);
      return res;
    });
  }

  function submitAssignment() {
    if (!entityId || !targetId) {
      setError("Pick an entity and a target.");
      return;
    }
    run(async () => {
      const res = await createAssignmentAction({
        entityId,
        targetType,
        targetId,
        roleLabel: roleLabel.trim(),
      });
      if (res.ok) {
        setAssignments((prev) => [res.assignment, ...prev]);
        setEntityId("");
        setTargetId("");
        setRoleLabel("");
      }
      return res;
    });
  }

  function removeAssignment(id) {
    run(async () => {
      const res = await deleteAssignmentAction(id);
      if (res.ok) setAssignments(res.assignments);
      return res;
    });
  }

  return (
    <div className="mt-6 space-y-6">
      {error && (
        <div className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm text-[#9B2335]">
          {error}
        </div>
      )}
      {pending && <p className="text-xs text-text-muted">Working…</p>}

      {/* Create team */}
      <Card title="Create a team" hint="Group staff who should share visibility.">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Name
            </label>
            <input
              className={`mt-1 w-56 ${inputClass()}`}
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
              placeholder="e.g. West Coast IR"
            />
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Description
            </label>
            <input
              className={`mt-1 w-72 ${inputClass()}`}
              value={teamDesc}
              onChange={(e) => setTeamDesc(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <button
            type="button"
            onClick={submitTeam}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Create team
          </button>
        </div>
      </Card>

      {/* Teams + members */}
      <Card title="Teams & members" hint="Add or remove members from each team.">
        {teams.length === 0 ? (
          <p className="text-sm text-text-muted">No teams yet.</p>
        ) : (
          <ul className="space-y-3">
            {teams.map((t) => (
              <li
                key={t.id}
                className="rounded-md border border-border bg-bg-app p-3"
              >
                <p className="text-sm font-medium text-text-primary">{t.name}</p>
                {t.description && (
                  <p className="text-xs text-text-muted">{t.description}</p>
                )}
                <div className="mt-2 flex flex-wrap gap-2">
                  {(t.members || []).length === 0 ? (
                    <span className="text-xs text-text-muted">No members.</span>
                  ) : (
                    t.members.map((m) => (
                      <span
                        key={m.user_id}
                        className="inline-flex items-center gap-1 rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy"
                      >
                        {m.full_name || m.email || m.user_id}
                        <button
                          type="button"
                          onClick={() => removeMember(t.id, m.user_id)}
                          className="text-navy/70 hover:text-navy"
                          aria-label="Remove member"
                        >
                          ×
                        </button>
                      </span>
                    ))
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-border pt-4">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Team
            </label>
            <select
              className={`mt-1 w-56 ${inputClass()}`}
              value={memberTeamId}
              onChange={(e) => setMemberTeamId(e.target.value)}
            >
              <option value="">Select a team…</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              User
            </label>
            <select
              className={`mt-1 w-64 ${inputClass()}`}
              value={memberUserId}
              onChange={(e) => setMemberUserId(e.target.value)}
            >
              <option value="">Select a user…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {userLabel(u)}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={submitMember}
            disabled={pending}
            className="rounded-md border border-navy px-4 py-2 text-sm font-medium text-navy hover:bg-navy hover:text-bg-app disabled:opacity-60"
          >
            Add member
          </button>
        </div>
      </Card>

      {/* Create assignment */}
      <Card
        title="Assign to an entity"
        hint="Assign a user or a team to an entity with a role label."
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Entity
            </label>
            <select
              className={`mt-1 w-64 ${inputClass()}`}
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
            >
              <option value="">Select an entity…</option>
              {entities.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.display_name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Assign to
            </label>
            <select
              className={`mt-1 w-32 ${inputClass()}`}
              value={targetType}
              onChange={(e) => {
                setTargetType(e.target.value);
                setTargetId("");
              }}
            >
              <option value="user">User</option>
              <option value="team">Team</option>
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              {targetType === "team" ? "Team" : "User"}
            </label>
            <select
              className={`mt-1 w-64 ${inputClass()}`}
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
            >
              <option value="">Select…</option>
              {(targetType === "team" ? teams : users).map((o) => (
                <option key={o.id} value={o.id}>
                  {targetType === "team" ? o.name : userLabel(o)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Role label
            </label>
            <input
              className={`mt-1 w-44 ${inputClass()}`}
              value={roleLabel}
              onChange={(e) => setRoleLabel(e.target.value)}
              placeholder="e.g. Relationship Mgr"
            />
          </div>
          <button
            type="button"
            onClick={submitAssignment}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Assign
          </button>
        </div>
      </Card>

      {/* Existing assignments */}
      <Card title="Assignments" hint="Records the staff-visibility resolver reads.">
        {assignments.length === 0 ? (
          <p className="text-sm text-text-muted">No assignments yet.</p>
        ) : (
          <div className="overflow-hidden rounded-md border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                  <th className="px-3 py-2 font-medium">Entity</th>
                  <th className="px-3 py-2 font-medium">Assigned to</th>
                  <th className="px-3 py-2 font-medium">Role</th>
                  <th className="px-3 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {assignments.map((a) => (
                  <tr
                    key={a.id}
                    className="border-b border-border last:border-b-0"
                  >
                    <td className="px-3 py-2 text-text-primary">
                      {a.entity_name || a.entity_id}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">
                      {a.assigned_to_team_id ? (
                        <span>
                          Team:{" "}
                          {a.assigned_to_team_name || a.assigned_to_team_id}
                        </span>
                      ) : (
                        <span>
                          {a.assigned_to_user_name || a.assigned_to_user_id}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-text-secondary">
                      {a.role_label || "—"}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => removeAssignment(a.id)}
                        className="text-sm font-medium text-[#9B2335] hover:underline"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
