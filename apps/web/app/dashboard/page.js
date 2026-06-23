import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import Footer from "@/components/Footer";

const MODULES = [
  {
    title: "Portfolio Summary",
    body: "Your holdings and allocation at a glance.",
  },
  {
    title: "Marketplace Activity",
    body: "New deals and recent movement across the marketplace.",
  },
  {
    title: "News Brief",
    body: "Curated updates relevant to your investment profile.",
  },
];

export default async function DashboardPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/dashboard");
  }

  const user = session.user;
  const name = user.name || user.nickname || user.email || "Member";

  return (
    <div className="flex min-h-screen flex-col bg-canvas">
      <TopBar user={user} />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-8">
          <h1 className="text-2xl font-semibold text-navy">
            Welcome back, {name}
          </h1>

          <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {MODULES.map((module) => (
              <section
                key={module.title}
                className="rounded-md border border-line border-l-4 border-l-gold bg-canvas p-6"
              >
                <h2 className="text-base font-semibold text-ink">
                  {module.title}
                </h2>
                <p className="mt-2 text-sm text-muted">{module.body}</p>
              </section>
            ))}
          </div>
        </main>
      </div>
      <Footer />
    </div>
  );
}
