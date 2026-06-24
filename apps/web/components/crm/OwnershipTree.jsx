import EntityTypeBadge from "@/components/EntityTypeBadge";

// Recursive, indented ownership tree built from the ownership-graph payload.
// direction "down": follow parent -> child edges (what this entity owns).
// direction "up":   follow child -> parent edges (who owns this entity).

function childEdges(edges, nodeId, direction) {
  return edges.filter((e) =>
    direction === "down" ? e.parent_id === nodeId : e.child_id === nodeId,
  );
}

function TreeNode({ nodeMap, edges, nodeId, direction, pct, ancestors }) {
  const node = nodeMap[nodeId];
  if (!node) return null;

  const nextAncestors = [...ancestors, nodeId];
  const children = childEdges(edges, nodeId, direction).filter((e) => {
    const otherId = direction === "down" ? e.child_id : e.parent_id;
    return !nextAncestors.includes(otherId); // guard against cycles
  });

  return (
    <li>
      <div className="flex items-center gap-2 py-1">
        <a
          href={`/crm/${nodeId}`}
          className="text-sm font-medium text-navy hover:underline"
        >
          {node.display_name}
        </a>
        <EntityTypeBadge type={node.entity_type} />
        {pct != null && (
          <span className="text-xs font-medium text-text-muted">
            {Number(pct).toFixed(2)}%
          </span>
        )}
      </div>
      {children.length > 0 && (
        <ul className="ml-4 border-l border-border pl-4">
          {children.map((edge) => {
            const otherId = direction === "down" ? edge.child_id : edge.parent_id;
            return (
              <TreeNode
                key={`${edge.parent_id}-${edge.child_id}`}
                nodeMap={nodeMap}
                edges={edges}
                nodeId={otherId}
                direction={direction}
                pct={edge.ownership_pct}
                ancestors={nextAncestors}
              />
            );
          })}
        </ul>
      )}
    </li>
  );
}

export default function OwnershipTree({ graph, rootId, direction, title }) {
  const edges = graph?.edges || [];
  const nodeMap = Object.fromEntries((graph?.nodes || []).map((n) => [n.id, n]));
  const hasChildren = childEdges(edges, rootId, direction).length > 0;

  return (
    <div>
      <h3 className="text-sm font-semibold text-text-secondary">{title}</h3>
      {hasChildren ? (
        <ul className="mt-2">
          <TreeNode
            nodeMap={nodeMap}
            edges={edges}
            nodeId={rootId}
            direction={direction}
            pct={null}
            ancestors={[]}
          />
        </ul>
      ) : (
        <p className="mt-2 text-sm text-text-muted">
          {direction === "down"
            ? "This entity does not own any other entities."
            : "No ownership records for this entity."}
        </p>
      )}
    </div>
  );
}
