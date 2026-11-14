import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI, getAdminUsers, getPermissionSets, getProfiles } from "@/lib/api";
import CustomFieldsBuilder from "@/components/admin/udf/CustomFieldsBuilder";
import { canWrite } from "@/lib/udfBuilder.mjs";

// UDFUI1 — Organization admin → Firm settings → Custom fields & tabs. The gate is the UDF routes' own: the admin reads
// (GET /udf/admin/definitions, /udf/admin/tabs) are internal + manage_portfolio — the permission every builder write
// needs. A refused read, or an envelope that does not say can_write, renders NO builder (fail closed). The profile,
// permission-set and people lists are read through their OWN admin routes and gates; a refused one is passed as null
// and the picker that needs it says so.
async function optional(fn) {
  try {
    return await fn();
  } catch {
    return null;
  }
}

export default async function CustomFieldsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/custom-fields");
  }

  let definitions = null;
  let tabs = null;
  let error = null;
  try {
    [definitions, tabs] = await Promise.all([
      fetchAPI("/api/v1/udf/admin/definitions"),
      fetchAPI("/api/v1/udf/admin/tabs"),
    ]);
  } catch (e) {
    error = e.status === 403 || e.status === 401 ? "forbidden" : e.message;
  }
  if (!error && !(canWrite(definitions) && canWrite(tabs))) error = "forbidden";

  let profiles = null;
  let permissionSets = null;
  let users = null;
  if (!error) {
    [profiles, permissionSets, users] = await Promise.all([
      optional(() => getProfiles()),
      optional(() => getPermissionSets()),
      optional(() => getAdminUsers()),
    ]);
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Custom fields &amp; tabs</h1>
        <p className="mt-1 text-sm text-text-muted">
          The firm&apos;s own fields on records, the tabs that show them, and who may see or edit each.
        </p>
      </div>
      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage custom fields.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load custom fields: {error}
        </div>
      ) : (
        <CustomFieldsBuilder initialDefinitions={definitions} initialTabs={tabs} profiles={profiles}
          permissionSets={permissionSets} users={Array.isArray(users) ? users : null} />
      )}
    </AppShell>
  );
}
