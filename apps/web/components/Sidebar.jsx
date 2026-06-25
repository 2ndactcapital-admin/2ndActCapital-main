"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import BrandNavIcon from "./BrandNavIcon";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard", icon: "dashboard" },
  { label: "Marketplace", href: "/marketplace", icon: "marketplace" },
  { label: "Investments", href: "/portfolio", icon: "portfolio" },
  {
    label: "Portfolio Reporting",
    href: "/portfolio-reporting",
    icon: "portfolio-reporting",
  },
  { label: "SPV Manager", href: "/spv-manager", icon: "spv-manager" },
  {
    label: "Investment Profile",
    href: "/investment-profile",
    icon: "investment-profile",
  },
  { label: "Insurance", href: "/insurance", icon: "insurance" },
  { label: "Community", href: "/community", icon: "community" },
];

const ADMIN_ITEM = { label: "Admin", href: "/admin", icon: "admin" };

function NavLink({ item, collapsed, active }) {
  const { label, href, icon } = item;
  return (
    <a
      href={href}
      title={collapsed ? label : undefined}
      className={`flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
        collapsed ? "justify-center" : "gap-3"
      }`}
      style={
        active
          ? { background: "rgba(232,213,163,0.12)" }
          : undefined
      }
    >
      <BrandNavIcon
        name={icon}
        size={20}
        className="shrink-0"
        style={{ color: active ? "var(--2a-gold-light)" : "#9AA6BF" }}
      />
      {!collapsed && (
        <span
          className="truncate"
          style={{ color: active ? "#FAF9F6" : "#9AA6BF" }}
        >
          {label}
        </span>
      )}
    </a>
  );
}

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();

  const isActive = (href) =>
    pathname === href || pathname?.startsWith(href + "/");

  return (
    <aside
      className={`flex shrink-0 flex-col bg-navy transition-[width] duration-200 ${
        collapsed ? "w-[52px]" : "w-[220px]"
      }`}
      style={{ borderRight: "0.5px solid rgba(255,255,255,0.08)" }}
    >
      <nav className="flex-1 space-y-1 p-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.href}
            item={item}
            collapsed={collapsed}
            active={isActive(item.href)}
          />
        ))}

        {/* Admin section */}
        {!collapsed && (
          <div
            className="px-3 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-wider"
            style={{ color: "rgba(154,166,191,0.6)" }}
          >
            Admin
          </div>
        )}
        <NavLink
          item={ADMIN_ITEM}
          collapsed={collapsed}
          active={isActive(ADMIN_ITEM.href)}
        />
      </nav>

      {/* Collapse toggle at the bottom */}
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="m-2 flex items-center justify-center rounded-md px-3 py-2 text-sm font-medium transition-colors"
        style={{ color: "#9AA6BF" }}
        onMouseEnter={(e) =>
          (e.currentTarget.style.background = "rgba(232,213,163,0.08)")
        }
        onMouseLeave={(e) => (e.currentTarget.style.background = "")}
      >
        {collapsed ? "»" : "«"}
      </button>
    </aside>
  );
}
