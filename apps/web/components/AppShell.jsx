import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import Footer from "@/components/Footer";
import AssistantPanel from "@/components/assistant/AssistantPanel";

// Full authenticated shell: TopBar + Sidebar + page content + AssistantPanel.
export default function AppShell({ user, children }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg-app">
      <TopBar user={user} />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto p-8">{children}</main>
        <AssistantPanel user={user} />
      </div>
      <Footer />
    </div>
  );
}
