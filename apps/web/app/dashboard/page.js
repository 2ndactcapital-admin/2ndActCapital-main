import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import Footer from "@/components/Footer";

const MODULES = [
  { label: "Portfolio Summary", value: "—" },
  { label: "Marketplace Activity", value: "—" },
  { label: "News Brief", value: "—" },
];

export default async function DashboardPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/dashboard");
  }

  const user = session.user;
  const name = user.name || user.nickname || user.email || "Member";

  return (
    <div className="flex min-h-screen flex-col bg-bg-app">
      <TopBar user={user} />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-8">
          <h1 className="text-2xl font-semibold text-text-primary">
            Welcome back, {name}
          </h1>

          <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {MODULES.map((module) => (
              <section
                key={module.label}
                className="rounded-md border-[0.5px] border-border border-l-[3px] border-l-gold bg-bg-card p-6"
              >
                <p className="text-sm text-text-muted">{module.label}</p>
                <p className="mt-2 text-2xl font-semibold text-navy">
                  {module.value}
                </p>
              </section>
            ))}
          </div>
        </main>
      </div>
      <Footer />
    </div>
  );
}
