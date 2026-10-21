import CommitmentProgress from "@/components/spv/CommitmentProgress";

// BUGFIX1 — dashboard pieces without hooks, rendered by DashboardBrief and by
// the node tests (tests/dashboard).

/** DASH-02 — a to-do's title links to where it is resolved (server `href`). */
export function TodoTitle({ item }) {
  if (!item.href) return item.title;
  return (
    <a href={item.href} className="hover:underline underline-offset-2" data-testid="todo-link">
      {item.title}
    </a>
  );
}

export function TodoItem({ item, onDismiss }) {
  const href = item.href || item.action_key;
  return (
    <div className="flex items-start justify-between gap-3 py-2 border-b border-[var(--2a-bg-sidebar)] last:border-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-[var(--2a-text)] truncate"><TodoTitle item={item} /></p>
        {item.detail && (
          <p className="text-xs text-[var(--2a-text-muted)] mt-0.5">{item.detail}</p>
        )}
      </div>
      <div className="flex gap-1.5 shrink-0">
        {href && (
          <a
            href={href}
            className="rounded px-2.5 py-1 text-xs font-medium"
            style={{ background: "var(--2a-navy)", color: "var(--2a-bg)" }}
          >
            {item.action_params || "Open"}
          </a>
        )}
        <button
          type="button"
          onClick={() => onDismiss?.(item.id)}
          className="rounded px-2 py-1 text-xs text-[var(--2a-text-muted)] hover:text-[var(--2a-text)] transition-colors"
          title="Dismiss"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

/** DASH-01 — the lifecycle stages (config labels, lifecycle order) from the
 *  same server function as the Marketplace summary bar. */
export function PipelineBlock({ data }) {
  const stages = (data?.stages || []).filter((s) => s.count > 0);
  if (!stages.length) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--2a-gold)] mb-3">
        Pipeline snapshot
      </h3>
      <dl className="grid grid-cols-2 gap-3">
        {stages.map((s) => (
          <div key={s.status} data-testid="pipeline-stage">
            <dt className="text-xs text-[var(--2a-text-muted)]">{s.label}</dt>
            <dd className="text-lg font-medium text-[var(--2a-text)] tabular-nums">{s.count}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function softLine(n) {
  // One string, so no renderer can drop the spaces between text nodes (DASH-03).
  return `${n} soft subscription${n === 1 ? "" : "s"} pending`;
}

/** DASH-03 — one line per SPV with its current state and committed money. */
export function SPVActivityBlock({ data }) {
  if (!data?.spvs?.length) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--2a-gold)] mb-3">
        SPV activity
      </h3>
      {data.soft_subscriptions > 0 && (
        <p className="text-sm text-[var(--2a-text-secondary)] mb-3" data-testid="soft-line">
          {softLine(data.soft_subscriptions)}
        </p>
      )}
      <ul className="space-y-3">
        {data.spvs.map((s) => (
          <li key={s.spv_id} data-testid="spv-line">
            <a href={`/spvs/${s.spv_id}`} className="text-sm font-medium text-[var(--2a-text)] hover:underline">
              {s.spv_name}
            </a>
            <span className="text-xs text-[var(--2a-text-muted)] capitalize">{` · ${s.status}`}</span>
            <CommitmentProgress commitment={s.commitment} compact />
          </li>
        ))}
      </ul>
    </div>
  );
}
