"use client";

import { usePermissions } from "@/lib/usePermissions";

// Renders the "New Deal" action only for users who can manage deals.
export default function NewDealButton() {
  const { can } = usePermissions();
  if (!can("manage_deals")) return null;
  return (
    <a
      href="/marketplace/new"
      className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
    >
      New Deal
    </a>
  );
}
