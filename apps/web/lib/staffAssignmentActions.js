"use server";

// Server actions for the SOC Phase 2 staff-visibility admin screen. Each wraps
// a server-side API call (auth enforced server-side by manage_members) and
// returns a plain {ok, ...} result the client component can render. These only
// populate assignment data — no visibility ENFORCEMENT change.

import {
  addStaffTeamMember,
  createStaffAssignment,
  createStaffTeam,
  deleteStaffAssignment,
  getStaffAssignments,
  getStaffTeams,
  removeStaffTeamMember,
} from "@/lib/api";

export async function createTeamAction(name, description) {
  try {
    const team = await createStaffTeam({ name, description: description || null });
    return { ok: true, team };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function addMemberAction(teamId, userId) {
  try {
    await addStaffTeamMember(teamId, userId);
    const teams = await getStaffTeams();
    return { ok: true, teams };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function removeMemberAction(teamId, userId) {
  try {
    await removeStaffTeamMember(teamId, userId);
    const teams = await getStaffTeams();
    return { ok: true, teams };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function createAssignmentAction(input) {
  try {
    const body = {
      entity_id: input.entityId,
      role_label: input.roleLabel || null,
    };
    if (input.targetType === "team") {
      body.assigned_to_team_id = input.targetId;
    } else {
      body.assigned_to_user_id = input.targetId;
    }
    const assignment = await createStaffAssignment(body);
    return { ok: true, assignment };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function deleteAssignmentAction(id) {
  try {
    await deleteStaffAssignment(id);
    const assignments = await getStaffAssignments();
    return { ok: true, assignments };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
