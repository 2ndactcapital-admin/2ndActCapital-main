"use client";

import { useRouter } from "next/navigation";
import OwnershipGraph from "./OwnershipGraph";

/**
 * Client wrapper that wires the shared <OwnershipGraph>'s Attio-style node
 * clicks to router navigation. Used by BOTH the staff and member routes so the
 * one rendering component is driven identically; only `apiBase` differs.
 *
 * Nodes navigate to the entity's own detail page (`${nodeHrefBase}/{id}`).
 * When `nodeQuery` is supplied it is appended as a query string, letting the
 * caller "walk" to a specific view — e.g. `nodeQuery="tab=ownership"` lands the
 * next entity on its Ownership tab rather than the generic CRM page.
 */
export default function EntityGraphNavigator({
  apiBase,
  title,
  emptyMessage,
  nodeHrefBase = "/crm",
  nodeQuery,
}) {
  const router = useRouter();
  return (
    <OwnershipGraph
      apiBase={apiBase}
      title={title}
      emptyMessage={emptyMessage}
      onNodeClick={(id) =>
        router.push(`${nodeHrefBase}/${id}${nodeQuery ? `?${nodeQuery}` : ""}`)
      }
    />
  );
}
