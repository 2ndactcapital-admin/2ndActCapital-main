import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import DashboardBrief from "@/components/dashboard/DashboardBrief";
import MyTasks from "@/components/dashboard/MyTasks";

function greeting(name) {
  const h = new Date().getHours();
  const time = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return `${time}, ${name}.`;
}

export default async function DashboardPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/dashboard");

  const user = session.user;
  const name = user.name || user.nickname || user.email?.split("@")[0] || "Member";

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl">
        <DashboardBrief greeting={greeting(name)} />
        {/* REP2 — open governed human tasks for this user (renders nothing when there are none). */}
        <div className="mt-6"><MyTasks /></div>
      </div>
    </AppShell>
  );
}
