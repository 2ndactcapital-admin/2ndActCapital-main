"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { IconAddressBook } from "@tabler/icons-react";
import BrandNavIcon from "./BrandNavIcon";
import { usePermissions } from "@/lib/usePermissions";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard", icon: "dashboard" },
  { label: "CRM", href: "/crm", TablerIcon: IconAddressBook },
  { label: "Marketplace", href: "/marketplace", icon: "marketplace" },
  { label: "Investments", href: "/portfolio", icon: "portfolio" },
  {
    label: "Portfolio Reporting",
    href: "/portfolio-reporting",
    icon: "portfolio-reporting",
  },
  { label: "SPV Manager", href: "/spv-manager", icon: "spv-manager" },
  { label: "Insurance", href: "/insurance", icon: "insurance" },
  { label: "Community", href: "/community", icon: "community" },
  { label: "Notifications", href: "/notifications", icon: "notifications" },
];

const ADMIN_ITEM = { label: "Admin", href: "/admin", icon: "admin" };
const USERS_ITEM = { label: "User Management", href: "/admin/users", icon: "investment-profile" };

function NavLink({ item, collapsed, active, badge = 0 }) {
  const { label, href, icon, TablerIcon } = item;
  const badgeText = badge > 9 ? "9+" : String(badge);
  const iconColor = active ? "var(--2a-gold-light)" : "#9AA6BF";
  return (
    <a
      href={href}
      title={collapsed ? label : undefined}
      className={`relative flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
        collapsed ? "justify-center" : "gap-3"
      }`}
      style={
        active
          ? { background: "rgba(232,213,163,0.12)" }
          : undefined
      }
    >
      <span className="relative shrink-0">
        {TablerIcon ? (
          <TablerIcon size={20} stroke={1.6} style={{ color: iconColor }} />
        ) : (
          <BrandNavIcon name={icon} size={20} style={{ color: iconColor }} />
        )}
        {collapsed && badge > 0 && (
          <span
            className="absolute -right-1.5 -top-1.5 flex min-w-[15px] items-center justify-center rounded-full px-1 text-[9px] font-semibold text-navy"
            style={{ backgroundColor: "#C5A880", height: 15 }}
          >
            {badgeText}
          </span>
        )}
      </span>
      {!collapsed && (
        <span
          className="flex flex-1 items-center justify-between truncate"
          style={{ color: active ? "#FAF9F6" : "#9AA6BF" }}
        >
          <span className="truncate">{label}</span>
          {badge > 0 && (
            <span
              className="ml-2 flex min-w-[18px] items-center justify-center rounded-full px-1.5 text-[10px] font-semibold text-navy"
              style={{ backgroundColor: "#C5A880", height: 16 }}
            >
              {badgeText}
            </span>
          )}
        </span>
      )}
    </a>
  );
}

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const [unread, setUnread] = useState(0);
  const pathname = usePathname();
  const { can } = usePermissions();

  const isActive = (href) =>
    pathname === href || pathname?.startsWith(href + "/");

  // Poll the unread notification count for the sidebar badge.
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const res = await fetch("/api/notifications/count", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (active) setUnread(data.unread_count ?? 0);
      } catch {
        // ignore
      }
    }
    refresh();
    const interval = setInterval(refresh, 60000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

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
            badge={item.href === "/notifications" ? unread : 0}
          />
        ))}

        {/* Admin section — gated by the manage_members permission. */}
        {can("manage_members") && (
          <>
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
            <NavLink
              item={USERS_ITEM}
              collapsed={collapsed}
              active={isActive(USERS_ITEM.href)}
            />
          </>
        )}
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
