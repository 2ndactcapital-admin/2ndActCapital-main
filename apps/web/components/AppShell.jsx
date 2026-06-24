import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import Footer from "@/components/Footer";

// Full authenticated shell: TopBar + Sidebar + Footer wrapping page content.
export default function AppShell({ user, children }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg-app">
      <TopBar user={user} />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-8">{children}</main>
      </div>
      <Footer />
    </div>
  );
}
