// PERMS1 (WF-03) — lay out the root's OWNERS above it in the hierarchy graph.
// The /tree API now returns `owners` (upward, both ownership tables, each owner
// with its own `owners` and an `effective_pct` look-through share of the root).
// Pure: positions + edges only, the same node / edge shapes HierarchyBuilder
// draws for children, so both look-through states show the same nodes.

export function layoutOwners(root, { nodeW = 160, nodeH = 52, hGap = 32, vGap = 80 } = {}) {
  const nodes = [];
  const edges = [];
  if (!root || !Array.isArray(root.owners) || root.owners.length === 0) return { nodes, edges };
  const seen = new Set([root.id]);
  let level = [{ node: root, x: 0 }];
  let depth = 0;
  while (level.length > 0 && depth < 20) {
    depth += 1;
    const next = [];
    const y = -depth * (nodeH + vGap);
    const above = [];
    for (const { node, x } of level) {
      for (const owner of Array.isArray(node.owners) ? node.owners : []) {
        above.push({ owner, child: node, childX: x });
      }
    }
    const width = above.length * nodeW + Math.max(0, above.length - 1) * hGap;
    let cx = -width / 2 + nodeW / 2;
    for (const { owner, child } of above) {
      edges.push({ parent: owner, child, pct: owner.ownership_pct ?? null });
      if (!seen.has(owner.id)) {
        seen.add(owner.id);
        nodes.push({ node: owner, x: cx, y });
        next.push({ node: owner, x: cx });
      }
      cx += nodeW + hGap;
    }
    level = next;
  }
  return { nodes, edges };
}

/** Effective (look-through) share of each owner in the root, keyed by id. */
export function ownerLookthrough(root) {
  const map = {};
  const walk = (n) => {
    for (const o of Array.isArray(n?.owners) ? n.owners : []) {
      if (o.effective_pct != null && map[o.id] == null) map[o.id] = o.effective_pct;
      walk(o);
    }
  };
  walk(root);
  return map;
}
