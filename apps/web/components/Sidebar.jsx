"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import {
  IconLayoutDashboard,
  IconAddressBook,
  IconBuildingStore,
  IconChartPie,
  IconChartTreemap,
  IconReportAnalytics,
  IconFileInvoice,
  IconUserCheck,
  IconShieldCheck,
  IconUsers,
  IconSitemap,
  IconSettings,
} from "@tabler/icons-react";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard", Icon: IconLayoutDashboard },
  { label: "CRM", href: "/crm", Icon: IconAddressBook },
  { label: "Marketplace", href: "/marketplace", Icon: IconBuildingStore },
  { label: "Investments", href: "/portfolio", Icon: IconChartPie },
  { label: "Allocations", href: "/portfolio?tab=allocation", Icon: IconChartTreemap },
  {
    label: "Portfolio Reporting",
    href: "/portfolio-reporting",
    Icon: IconReportAnalytics,
  },
  { label: "Taxonomy", href: "/taxonomy", Icon: IconSitemap },
  { label: "SPV Manager", href: "/spv-manager", Icon: IconFileInvoice },
  {
    label: "Investment Profile",
    href: "/investment-profile",
    Icon: IconUserCheck,
  },
  { label: "Insurance", href: "/insurance", Icon: IconShieldCheck },
  { label: "Community", href: "/community", Icon: IconUsers },
];

const ADMIN_ITEM = { label: "Admin", href: "/admin", Icon: IconSettings };

function NavLink({ item, collapsed, active }) {
  const { label, href, Icon } = item;
  return (
    <a
      href={href}
      title={collapsed ? label : undefined}
      className={`flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
        collapsed ? "justify-center" : "gap-3"
      } ${active ? "bg-navy" : "hover:bg-border"}`}
    >
      <Icon
        size={20}
        stroke={1.75}
        className={`shrink-0 ${active ? "text-gold" : "text-text-secondary"}`}
      />
      {!collapsed && (
        <span
          className={`truncate ${active ? "text-bg-app" : "text-text-secondary"}`}
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
      className={`flex shrink-0 flex-col border-r-[0.5px] border-border bg-bg-sidebar transition-[width] duration-200 ${
        collapsed ? "w-[52px]" : "w-[220px]"
      }`}
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
          <div className="px-3 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
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
        className="m-2 flex items-center justify-center rounded-md px-3 py-2 text-sm font-medium text-text-muted transition-colors hover:bg-border"
      >
        {collapsed ? "»" : "«"}
      </button>
    </aside>
  );
}
