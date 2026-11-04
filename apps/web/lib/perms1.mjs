// PERMS1 — the front-end mirrors of the deal / household creation gates and the
// workflow-delete rule. Pure functions of the /users/me envelope (or the API's
// own row fields), fail closed: a lost or malformed envelope shows no control.
import { canPermStrict, isInternalStrict, isStaffStrict } from "./menuVisibility.mjs";

/** DEAL-01 — "New deal": an internal firm role holding manage_deals (API
 * create_deal: require_internal + require_permission("manage_deals")). */
export function canCreateDeal(me) {
  return isInternalStrict(me) && canPermStrict(me, "manage_deals");
}

/** REP-01 — "New household" / "Add account": an internal firm role holding
 * manage_members, or staff (API routers/households _require_household_admin:
 * require_internal_permission). The
 * management screen itself renders from the API envelope's
 * permissions.can_write; this only decides whether to ask for it. */
export function canManageHouseholds(me) {
  return isInternalStrict(me) && (isStaffStrict(me) || canPermStrict(me, "manage_members"));
}
