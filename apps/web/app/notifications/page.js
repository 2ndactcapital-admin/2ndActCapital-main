import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import NotificationsFeed from "@/components/NotificationsFeed";
import { getNotifications } from "@/lib/api";

export default async function NotificationsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/notifications");
  }

  let initialItems = [];
  try {
    const data = await getNotifications({ limit: 20, offset: 0 });
    initialItems = data?.notifications || [];
  } catch {
    initialItems = [];
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Notifications</h1>
        <p className="mt-1 text-sm text-text-muted">
          Activity across your deals, compliance reviews, and documents
        </p>
      </div>
      <NotificationsFeed initialItems={initialItems} />
    </AppShell>
  );
}
