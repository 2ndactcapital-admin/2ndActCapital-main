import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import Footer from "@/components/Footer";
import AssistantPanel from "@/components/assistant/AssistantPanel";
import TabbedSection from "@/components/TabbedSection";
import AccessGate from "@/components/AccessGate";

// Sidebar-first layout: Sidebar spans full height on the left; TopBar,
// main content, AssistantPanel, and Footer fill the remaining column.
// navregroup: TabbedSection draws a master page's tab bar above any page that is
// one of its tabs (and nothing elsewhere), so the tab pages need no edits.
// AUTHZ1: AccessGate swaps the page for the "Access pending" notice when
// /users/me reports an account that is not admitted.
export default function AppShell({ user, children }) {
  return (
    <div className="flex h-screen bg-bg-app overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar user={user} />
        <div className="flex flex-1 overflow-hidden">
          <main className="flex-1 overflow-y-auto p-8">
            <AccessGate>
              <TabbedSection />
              {children}
            </AccessGate>
          </main>
          <AssistantPanel user={user} />
        </div>
        <Footer />
      </div>
    </div>
  );
}
