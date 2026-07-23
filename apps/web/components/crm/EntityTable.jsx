"use client";

import { useMemo } from "react";
import DataGrid from "@/components/ui/DataGrid";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import { typeLabel } from "@/lib/entityTypes";

// Migrated onto the shared DataGrid (Grid UX mini-sprint B). All the prior
// behaviour is preserved — the clickable name link, the entity-type badge,
// the em-dash fallbacks, the localized created date, and sortable columns —
// and the grid adds quick-search / column-picker / column-reorder on top.
//
// `entity_type` is sorted by its human label (as before), so we surface a
// derived `entity_type_label` field for the grid to sort/search on while the
// cell itself still renders the badge from the raw `entity_type`.
const columnDefs = [
  {
    field: "display_name",
    headerName: "Name",
    cell: (value, row) => (
      <a
        href={`/crm/${row.id}`}
        className="font-medium text-navy hover:underline"
      >
        {value}
      </a>
    ),
  },
  {
    field: "entity_type_label",
    headerName: "Type",
    cell: (_value, row) => <EntityTypeBadge type={row.entity_type} />,
  },
  {
    field: "country_of_formation",
    headerName: "Country",
    cell: (value) => value || "—",
  },
  {
    field: "created_at",
    headerName: "Created",
    cell: (value) => (
      <span className="text-text-muted">
        {value ? new Date(value).toLocaleDateString() : "—"}
      </span>
    ),
  },
];

export default function EntityTable({ entities }) {
  const rowData = useMemo(
    () =>
      [...entities]
        .map((entity) => ({
          ...entity,
          entity_type_label: typeLabel(entity.entity_type),
        }))
        // Preserve the previous default view: name, ascending.
        .sort((a, b) =>
          String(a.display_name || "").localeCompare(
            String(b.display_name || ""),
          ),
        ),
    [entities],
  );

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <DataGrid
        gridId="crm-entities"
        columnDefs={columnDefs}
        rowData={rowData}
        getRowId={(row) => row.id}
        quickFilterPlaceholder="Search entities…"
        emptyMessage="No entities to display."
      />
    </div>
  );
}
