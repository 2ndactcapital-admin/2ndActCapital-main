import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import Footer from "@/components/Footer";
import AppearanceSettings from "@/components/AppearanceSettings";

export default async function SettingsPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/settings");
  }

  return (
    <div className="flex min-h-screen flex-col bg-bg-app">
      <TopBar user={session.user} />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-8">
          <h1 className="text-2xl font-semibold text-navy">Settings</h1>
          <div className="mt-8 max-w-3xl">
            <AppearanceSettings />
          </div>
        </main>
      </div>
      <Footer />
    </div>
  );
}
