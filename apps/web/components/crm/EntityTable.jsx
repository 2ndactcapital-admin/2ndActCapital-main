"use client";

import { useState, useMemo } from "react";
import { IconArrowUp, IconArrowDown } from "@tabler/icons-react";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import { typeLabel } from "@/lib/entityTypes";

const COLUMNS = [
  { key: "display_name", label: "Name" },
  { key: "entity_type", label: "Type" },
  { key: "country_of_formation", label: "Country" },
  { key: "created_at", label: "Created" },
];

function sortValue(entity, key) {
  if (key === "entity_type") return typeLabel(entity.entity_type).toLowerCase();
  const value = entity[key];
  return value ? String(value).toLowerCase() : "";
}

export default function EntityTable({ entities }) {
  const [sortKey, setSortKey] = useState("display_name");
  const [asc, setAsc] = useState(true);

  const sorted = useMemo(() => {
    const copy = [...entities];
    copy.sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      if (av < bv) return asc ? -1 : 1;
      if (av > bv) return asc ? 1 : -1;
      return 0;
    });
    return copy;
  }, [entities, sortKey, asc]);

  function toggleSort(key) {
    if (key === sortKey) {
      setAsc((value) => !value);
    } else {
      setSortKey(key);
      setAsc(true);
    }
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-bg-card">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border">
            {COLUMNS.map((col) => (
              <th key={col.key} className="px-4 py-3 font-medium text-text-secondary">
                <button
                  type="button"
                  onClick={() => toggleSort(col.key)}
                  className="inline-flex items-center gap-1 hover:text-navy"
                >
                  {col.label}
                  {sortKey === col.key &&
                    (asc ? (
                      <IconArrowUp size={14} />
                    ) : (
                      <IconArrowDown size={14} />
                    ))}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((entity) => (
            <tr
              key={entity.id}
              className="border-b border-border last:border-0 hover:bg-bg-app"
            >
              <td className="px-4 py-3">
                <a
                  href={`/crm/${entity.id}`}
                  className="font-medium text-navy hover:underline"
                >
                  {entity.display_name}
                </a>
              </td>
              <td className="px-4 py-3">
                <EntityTypeBadge type={entity.entity_type} />
              </td>
              <td className="px-4 py-3 text-text-secondary">
                {entity.country_of_formation || "—"}
              </td>
              <td className="px-4 py-3 text-text-muted">
                {entity.created_at
                  ? new Date(entity.created_at).toLocaleDateString()
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
