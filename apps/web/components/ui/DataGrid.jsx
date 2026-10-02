"use client";

/**
 * DataGrid — the shared table component (Grid UX mini-sprint A).
 *
 * Headless state (sort / filter / pagination / column visibility / column
 * order) is owned by TanStack Table (@tanstack/react-table, MIT). Column
 * drag-reorder is driven by @dnd-kit layered on top of TanStack's
 * columnOrder state. Nothing here uses AG-Grid.
 *
 * The markup is hand-rolled and styled exclusively through the `--2a-*`
 * design tokens (navy / gold / cream), which the root layout injects from
 * the tenant's org_settings (Sprint 24 white-label). No Signature-palette
 * hex is hardcoded — the grid is theme-driven from day one.
 *
 * External API is deliberately AG-Grid-shaped so callers stay stable:
 *   columnDefs  — [{ field, headerName, cell?, align?, enableSorting?,
 *                    enableColumnFilter?, filterPlaceholder?, minWidth? }]
 *   rowData     — array of row objects
 *   gridId      — stable id reserved for saved layout preferences. Accepted
 *                 now so the prop API is frozen; persistence is a later
 *                 mini-sprint and is intentionally NOT implemented here.
 *   getRowStyle — optional (row) => style object, applied to the <tr>. Purely
 *                 additive and undefined by default, so existing callers are
 *                 untouched. Added by Portfolio UX 2, which has a row KIND —
 *                 a corporate-action adjustment — that Phase F requires must
 *                 never look like an ordinary trade. A per-cell renderer can
 *                 only mark the cell it owns; "this whole row is a different
 *                 kind of thing" is a row-level fact and needs a row-level
 *                 hook. Styling only: it cannot change behaviour or content.
 *   serverSide  — optional. When set, the SERVER owns sort and paging
 *                 (edgarpipelinea: ~717K manifest rows cannot load into the
 *                 browser). Shape: { totalRows, pageIndex, pageSize, sorting:
 *                 [{ id, desc }], onSortingChange(sorting), onPageChange(i),
 *                 loading }. `rowData` is then just the current page. TanStack
 *                 runs in manual mode, the global and per-column text filters
 *                 are hidden (they could only filter the visible page, which
 *                 would look like a real filter and not be one), and the footer
 *                 counts `totalRows`. Undefined by default, so every existing
 *                 caller keeps exactly its client-side behaviour.
 */

import { useMemo, useState, useRef, useEffect } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
} from "@tanstack/react-table";
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
} from "@dnd-kit/core";
import {
  SortableContext,
  horizontalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { restrictToHorizontalAxis } from "@dnd-kit/modifiers";
import { CSS } from "@dnd-kit/utilities";

// ─── Header cell (sortable + drag-reorderable) ───────────────────────────────

function HeaderCell({ header, enableReorder }) {
  const column = header.column;
  const align = column.columnDef.meta?.align || "left";
  const canSort = column.getCanSort();
  const sortDir = column.getIsSorted(); // false | "asc" | "desc"

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: column.id, disabled: !enableReorder });

  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    textAlign: align,
    whiteSpace: "nowrap",
  };

  return (
    <th
      ref={setNodeRef}
      style={style}
      className="px-3 py-2 font-semibold text-[var(--2a-text-muted)] select-none"
    >
      <div
        className={`flex items-center gap-1 ${
          align === "right" ? "justify-end" : "justify-start"
        }`}
      >
        {enableReorder && (
          <span
            {...attributes}
            {...listeners}
            aria-label="Drag to reorder column"
            className="cursor-grab text-[var(--2a-text-muted)] opacity-40 hover:opacity-100"
            style={{ touchAction: "none" }}
          >
            ⠿
          </span>
        )}
        <button
          type="button"
          disabled={!canSort}
          onClick={canSort ? column.getToggleSortingHandler() : undefined}
          className={`flex items-center gap-1 ${
            canSort ? "cursor-pointer hover:text-[var(--2a-navy)]" : "cursor-default"
          }`}
        >
          {flexRender(column.columnDef.header, header.getContext())}
          {sortDir === "asc" && <span className="text-[var(--2a-gold)]">▲</span>}
          {sortDir === "desc" && <span className="text-[var(--2a-gold)]">▼</span>}
        </button>
      </div>
    </th>
  );
}

// ─── Column picker menu ───────────────────────────────────────────────────────

function ColumnPicker({ table }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)] hover:bg-[var(--2a-bg)]"
      >
        Columns
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-56 rounded border border-[var(--2a-border)] bg-[var(--2a-bg-card)] p-2 shadow-[0_1px_3px_rgba(0,0,0,0.06)]">
          {table.getAllLeafColumns().map((column) => (
            <label
              key={column.id}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-[var(--2a-text-secondary)] hover:bg-[var(--2a-bg)]"
            >
              <input
                type="checkbox"
                checked={column.getIsVisible()}
                disabled={!column.getCanHide()}
                onChange={column.getToggleVisibilityHandler()}
                className="accent-[var(--2a-navy)]"
              />
              {column.columnDef.meta?.headerName || column.id}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function DataGrid({
  columnDefs = [],
  rowData = [],
  gridId,
  getRowId,
  onRowClick,
  selectedRowId,
  quickFilterPlaceholder = "Search…",
  emptyMessage = "No rows to display.",
  enableColumnPicker = true,
  enableColumnReorder = true,
  enableGlobalFilter = true,
  enablePagination = true,
  pageSize = 25,
  getRowStyle,
  serverSide,
}) {
  // gridId is reserved for a future saved-layout feature. Referenced here so
  // the prop is part of the stable API; no persistence is wired up yet.
  void gridId;

  const columns = useMemo(
    () =>
      columnDefs.map((def) => ({
        id: def.field,
        accessorKey: def.field,
        header: def.headerName ?? def.field,
        enableSorting: def.enableSorting !== false,
        enableColumnFilter: def.enableColumnFilter === true,
        cell: (ctx) =>
          def.cell
            ? def.cell(ctx.getValue(), ctx.row.original)
            : ctx.getValue() ?? "",
        meta: {
          align: def.align || "left",
          headerName: def.headerName ?? def.field,
          filterPlaceholder: def.filterPlaceholder,
          minWidth: def.minWidth,
        },
      })),
    [columnDefs],
  );

  const [sorting, setSorting] = useState([]);
  const [columnFilters, setColumnFilters] = useState([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [columnVisibility, setColumnVisibility] = useState({});
  const [columnOrder, setColumnOrder] = useState(() =>
    columnDefs.map((d) => d.field),
  );
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize });

  const server = serverSide || null;
  const serverPagination = server
    ? { pageIndex: server.pageIndex, pageSize: server.pageSize }
    : null;

  const table = useReactTable({
    data: rowData,
    columns,
    state: {
      sorting: server ? server.sorting : sorting,
      columnFilters,
      globalFilter,
      columnVisibility,
      columnOrder,
      ...(enablePagination ? { pagination: server ? serverPagination : pagination } : {}),
    },
    getRowId,
    onSortingChange: server
      ? (updater) =>
          server.onSortingChange(
            typeof updater === "function" ? updater(server.sorting) : updater,
          )
      : setSorting,
    onColumnFiltersChange: setColumnFilters,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    onPaginationChange: server
      ? (updater) => {
          const next =
            typeof updater === "function" ? updater(serverPagination) : updater;
          if (next.pageIndex !== server.pageIndex) server.onPageChange(next.pageIndex);
        }
      : setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    ...(enablePagination ? { getPaginationRowModel: getPaginationRowModel() } : {}),
    ...(server
      ? {
          manualSorting: true,
          manualFiltering: true,
          manualPagination: true,
          enableSortingRemoval: false,
          pageCount: Math.max(1, Math.ceil(server.totalRows / server.pageSize)),
        }
      : {}),
  });

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  function handleDragEnd(event) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setColumnOrder((prev) => {
      const order = prev.length ? prev : columns.map((c) => c.id);
      const from = order.indexOf(active.id);
      const to = order.indexOf(over.id);
      if (from === -1 || to === -1) return order;
      const next = [...order];
      next.splice(to, 0, next.splice(from, 1)[0]);
      return next;
    });
  }

  const headerGroups = table.getHeaderGroups();
  const rows = table.getRowModel().rows;
  const anyColumnFilters = !server && columns.some((c) => c.enableColumnFilter);
  const showGlobalFilter = enableGlobalFilter && !server;

  // The currently-visible, ordered leaf column ids for the SortableContext.
  const orderedIds = table.getVisibleLeafColumns().map((c) => c.id);

  return (
    <div className="flex flex-col">
      {/* Toolbar */}
      {(showGlobalFilter || enableColumnPicker) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {showGlobalFilter ? (
            <input
              type="text"
              value={globalFilter ?? ""}
              onChange={(e) => setGlobalFilter(e.target.value)}
              placeholder={quickFilterPlaceholder}
              className="w-56 rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
            />
          ) : (
            <span />
          )}
          {enableColumnPicker && <ColumnPicker table={table} />}
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          modifiers={[restrictToHorizontalAxis]}
          onDragEnd={enableColumnReorder ? handleDragEnd : undefined}
        >
          <table className="w-full text-xs">
            <thead className="border-b border-[var(--2a-border)]">
              {headerGroups.map((hg) => (
                <tr key={hg.id}>
                  <SortableContext
                    items={orderedIds}
                    strategy={horizontalListSortingStrategy}
                  >
                    {hg.headers.map((header) => (
                      <HeaderCell
                        key={header.id}
                        header={header}
                        enableReorder={enableColumnReorder}
                      />
                    ))}
                  </SortableContext>
                </tr>
              ))}
              {anyColumnFilters && (
                <tr className="border-b border-[var(--2a-border)]">
                  {table.getVisibleLeafColumns().map((column) => (
                    <th key={column.id} className="px-3 pb-2">
                      {column.getCanFilter() ? (
                        <input
                          type="text"
                          value={(column.getFilterValue() ?? "")}
                          onChange={(e) => column.setFilterValue(e.target.value)}
                          placeholder={
                            column.columnDef.meta?.filterPlaceholder || "Filter…"
                          }
                          className="w-full rounded border border-[var(--2a-border)] px-2 py-1 text-[10px] font-normal text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                        />
                      ) : null}
                    </th>
                  ))}
                </tr>
              )}
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={table.getVisibleLeafColumns().length || 1}
                    className="px-3 py-8 text-center text-[var(--2a-text-muted)]"
                  >
                    {emptyMessage}
                  </td>
                </tr>
              ) : (
                rows.map((row) => {
                  const selected =
                    selectedRowId != null && row.id === String(selectedRowId);
                  return (
                    <tr
                      key={row.id}
                      onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                      style={getRowStyle ? getRowStyle(row.original) : undefined}
                      className={`border-t border-[var(--2a-border)] ${
                        onRowClick ? "cursor-pointer hover:bg-[var(--2a-bg)]" : ""
                      } ${selected ? "bg-[var(--2a-bg)]" : ""}`}
                    >
                      {row.getVisibleCells().map((cell) => {
                        const align = cell.column.columnDef.meta?.align || "left";
                        return (
                          <td
                            key={cell.id}
                            className={`px-3 py-2 ${
                              align === "right"
                                ? "text-right tabular-nums text-[var(--2a-text)]"
                                : align === "center"
                                  ? "text-center text-[var(--2a-text-secondary)]"
                                  : "text-left text-[var(--2a-text-secondary)]"
                            }`}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </DndContext>
      </div>

      {/* Pagination */}
      {enablePagination &&
        (server
          ? server.totalRows > server.pageSize
          : rowData.length > pagination.pageSize) && (
        <div className="mt-3 flex items-center justify-between text-[10px] text-[var(--2a-text-muted)]">
          <span>
            {server
              ? `${server.totalRows.toLocaleString()} row${server.totalRows === 1 ? "" : "s"}${server.loading ? " — loading…" : ""}`
              : `${table.getFilteredRowModel().rows.length} row${table.getFilteredRowModel().rows.length === 1 ? "" : "s"}`}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="rounded border border-[var(--2a-border)] px-2 py-1 hover:bg-[var(--2a-bg)] disabled:opacity-40"
            >
              Prev
            </button>
            <span>
              Page {table.getState().pagination.pageIndex + 1} of{" "}
              {table.getPageCount() || 1}
            </span>
            <button
              type="button"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="rounded border border-[var(--2a-border)] px-2 py-1 hover:bg-[var(--2a-bg)] disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
