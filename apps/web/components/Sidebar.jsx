"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard" },
  { label: "Marketplace", href: "/marketplace" },
  { label: "Portfolio", href: "/portfolio" },
  { label: "SPV Manager", href: "/spv-manager" },
  { label: "Investment Profile", href: "/investment-profile" },
  { label: "Insurance", href: "/insurance" },
  { label: "Community", href: "/community" },
  { label: "Admin", href: "/admin" },
];

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();

  return (
    <aside
      className={`flex shrink-0 flex-col border-r border-line bg-sand transition-[width] duration-200 ${
        collapsed ? "w-16" : "w-60"
      }`}
    >
      <nav className="flex-1 space-y-1 p-2">
        {NAV_ITEMS.map((item) => {
          const active =
            pathname === item.href || pathname?.startsWith(item.href + "/");
          return (
            <a
              key={item.href}
              href={item.href}
              title={collapsed ? item.label : undefined}
              className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                active
                  ? "bg-navy text-white"
                  : "text-ink-soft hover:bg-line"
              }`}
            >
              <span
                className={`flex h-5 w-5 shrink-0 items-center justify-center text-xs font-semibold ${
                  active ? "text-white" : "text-muted"
                }`}
                aria-hidden="true"
              >
                {item.label[0]}
              </span>
              {!collapsed && <span className="truncate">{item.label}</span>}
            </a>
          );
        })}
      </nav>

      {/* Collapse toggle at the bottom */}
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="m-2 flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted transition-colors hover:bg-line"
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center">
          {collapsed ? "»" : "«"}
        </span>
        {!collapsed && <span>Collapse</span>}
      </button>
    </aside>
  );
}
