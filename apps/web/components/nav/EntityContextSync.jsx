"use client";

import { Suspense, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useEntityContext } from "@/lib/useEntityContext";
import { shouldAdoptRemembered, withEntity } from "@/lib/entityContext.mjs";

// NAV2 — when the URL carries no entity_id and this person has a remembered selection, write it INTO the URL
// (router.replace — no new history entry) so the page, the chip and every link agree. The URL always wins: a URL
// that carries entity_id (even an empty one) is never overridden. Renders nothing.
function Sync() {
  const router = useRouter();
  const pathname = usePathname() || "/";
  const { selection } = useEntityContext();
  useEffect(() => {
    if (!shouldAdoptRemembered(selection)) return;
    router.replace(withEntity(`${pathname}${window.location.search}${window.location.hash}`, selection.id));
  }, [selection.source, selection.id, pathname, router]);
  return null;
}

export default function EntityContextSync() {
  return (
    <Suspense fallback={null}>
      <Sync />
    </Suspense>
  );
}
