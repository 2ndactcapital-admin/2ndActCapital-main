import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import DashboardBrief from "@/components/dashboard/DashboardBrief";

function greeting(name) {
  const h = new Date().getHours();
  const time = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return `${time}, ${name}.`;
}

export default async function DashboardPage() {
  const session = await auth0.getSession();
  if (!session) redirect("/auth/login?returnTo=/dashboard");

  const user = session.user;
  const name = user.name || user.nickname || user.email?.split("@")[0] || "Member";

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl">
        <DashboardBrief greeting={greeting(name)} />
      </div>
    </AppShell>
  );
}
