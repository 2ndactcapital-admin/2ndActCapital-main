"use client";

import AccessPending, { isAccessPending } from "@/components/AccessPending";
import { usePermissions } from "@/lib/usePermissions";

// AUTHZ1 — renders the page only for an admitted account. /users/me is the one
// API a pending account may call, and it carries `status` from the DB
// Principal; anything other than 'active' gets the pending page instead.
export default function AccessGate({ children }) {
  // Children render while /users/me loads (the server refuses a pending
  // account's data calls regardless); the notice replaces them once known.
  const { me } = usePermissions();
  if (isAccessPending(me)) return <AccessPending me={me} />;
  return children;
}
