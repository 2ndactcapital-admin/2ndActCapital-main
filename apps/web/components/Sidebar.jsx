"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { IconAddressBook } from "@tabler/icons-react";
import BrandNavIcon from "./BrandNavIcon";
import { useBrand } from "@/components/ThemeProvider";
import { usePermissions } from "@/lib/usePermissions";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard", icon: "dashboard" },
  { label: "CRM", href: "/crm", TablerIcon: IconAddressBook },
  { label: "Marketplace", href: "/marketplace", icon: "marketplace" },
  { label: "Investments", href: "/portfolio", icon: "portfolio" },
  { label: "Portfolio Reporting", href: "/portfolio-reporting", icon: "portfolio-reporting" },
  { label: "SPV Manager", href: "/spvs", icon: "spv-manager" },
  { label: "Insurance", href: "/insurance", icon: "insurance" },
  { label: "Community", href: "/community", icon: "community" },
  { label: "Notifications", href: "/notifications", icon: "notifications" },
];

const ADMIN_ITEM = { label: "Admin", href: "/admin", icon: "admin" };
const USERS_ITEM = { label: "User Management", href: "/admin/users", icon: "investment-profile" };
// Sprint 24 — white-label settings. Org Admins see their own org; Super
// Admins additionally get the platform-wide screen.
const ORG_SETTINGS_ITEM = { label: "Organization", href: "/admin/settings", icon: "admin" };
const PLATFORM_ITEM = { label: "Platform", href: "/admin/platform", icon: "admin" };

// The Ascent mark inline SVG — white on navy, with gold-light top square.
function AscendMark({ size = 20 }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 512 512"
      width={size}
      height={size}
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      <rect x="118" y="300" width="80" height="80" rx="20" fill="rgba(255,255,255,0.65)" />
      <rect x="216" y="216" width="80" height="80" rx="20" fill="rgba(255,255,255,0.85)" />
      <rect x="314" y="132" width="80" height="80" rx="20" fill="var(--2a-gold-light)" />
    </svg>
  );
}

function NavLink({ item, expanded, active, badge = 0 }) {
  const { label, href, icon, TablerIcon } = item;
  const badgeText = badge > 9 ? "9+" : String(badge);
  const iconColor = active ? "var(--2a-gold-light, var(--2a-gold-light))" : "var(--2a-nav-rest)";
  return (
    <a
      href={href}
      title={!expanded ? label : undefined}
      className={`relative flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
        expanded ? "gap-3" : "justify-center"
      }`}
      style={
        active
          ? { background: "color-mix(in srgb, var(--2a-gold-light) 12%, transparent)" }
          : undefined
      }
    >
      <span className="relative shrink-0">
        {TablerIcon ? (
          <TablerIcon size={20} stroke={1.6} style={{ color: iconColor }} />
        ) : (
          <BrandNavIcon name={icon} size={20} style={{ color: iconColor }} />
        )}
        {!expanded && badge > 0 && (
          <span
            className="absolute -right-1.5 -top-1.5 flex min-w-[15px] items-center justify-center rounded-full px-1 text-[9px] font-semibold"
            style={{ backgroundColor: "var(--2a-gold)", color: "var(--2a-navy)", height: 15 }}
          >
            {badgeText}
          </span>
        )}
      </span>
      {expanded && (
        <span
          className="flex flex-1 items-center justify-between truncate"
          style={{ color: active ? "var(--2a-bg)" : "var(--2a-nav-rest)" }}
        >
          <span className="truncate">{label}</span>
          {badge > 0 && (
            <span
              className="ml-2 flex min-w-[18px] items-center justify-center rounded-full px-1.5 text-[10px] font-semibold"
              style={{ backgroundColor: "var(--2a-gold)", color: "var(--2a-navy)", height: 16 }}
            >
              {badgeText}
            </span>
          )}
        </span>
      )}
    </a>
  );
}

function PinIcon({ pinned }) {
  // Simple pin icon — diagonal line with dot
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={14}
      height={14}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {pinned ? (
        <>
          <line x1="12" y1="17" x2="12" y2="22" />
          <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
        </>
      ) : (
        <>
          <line x1="12" y1="17" x2="12" y2="22" />
          <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" opacity={0.45} />
        </>
      )}
    </svg>
  );
}

export default function Sidebar() {
  // Seed from localStorage immediately to avoid a flash on load.
  const [pinned, setPinned] = useState(() => {
    try {
      const stored = localStorage.getItem("nav-pinned");
      return stored === "true";
    } catch {
      return false;
    }
  });
  const [hovered, setHovered] = useState(false);
  const [unread, setUnread] = useState(0);
  const pathname = usePathname();
  const brand = useBrand();
  // navPinned comes from /api/users/me (cached by usePermissions).
  const { can, navPinned, accountRole: role } = usePermissions();
  const mouseLeaveTimer = useRef(null);
  // Track whether the account value has been applied so we only sync once.
  const accountSynced = useRef(false);

  // When the account value loads, let it override local state once.
  useEffect(() => {
    if (accountSynced.current || navPinned === null || navPinned === undefined) return;
    accountSynced.current = true;
    setPinned(navPinned);
    try { localStorage.setItem("nav-pinned", String(navPinned)); } catch {}
  }, [navPinned]);

  function togglePin() {
    const next = !pinned;
    setPinned(next);
    // Update localStorage immediately — no flash on next navigation.
    try { localStorage.setItem("nav-pinned", String(next)); } catch {}
    // Persist to account so the setting follows the user across devices.
    fetch("/api/users/me", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nav_pinned: next }),
    }).catch(() => {});
  }

  function handleMouseEnter() {
    if (mouseLeaveTimer.current) clearTimeout(mouseLeaveTimer.current);
    if (!pinned) setHovered(true);
  }

  function handleMouseLeave() {
    if (!pinned) {
      // Small delay prevents jitter when moving between items.
      mouseLeaveTimer.current = setTimeout(() => setHovered(false), 80);
    }
  }

  // When pinned, always expanded. Otherwise expanded only while hovered.
  const expanded = pinned || hovered;

  const isActive = (href) =>
    pathname === href || pathname?.startsWith(href + "/");

  // Unread notification badge — poll every 60 s.
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const res = await fetch("/api/notifications/count", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (active) setUnread(data.unread_count ?? 0);
      } catch {}
    }
    refresh();
    const id = setInterval(refresh, 60000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  return (
    // The outer aside reserves exactly the icon-rail width (52 px) in the flex
    // layout when unpinned, so it never pushes main content during hover.
    // When pinned it reserves the full 220 px (pushes content, as expected).
    <aside
      className="relative flex-shrink-0"
      style={{ width: pinned ? 220 : 52 }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Inner panel: absolute so it can fly out over content when hovered. */}
      <div
        className="absolute inset-y-0 left-0 flex flex-col bg-navy overflow-hidden transition-all duration-200"
        style={{
          width: expanded ? 220 : 52,
          zIndex: hovered && !pinned ? 50 : "auto",
          boxShadow: hovered && !pinned ? "4px 0 16px rgba(0,0,0,0.18)" : "none",
          borderRight: "0.5px solid rgba(255,255,255,0.08)",
        }}
      >
        {/* Sidebar header: mark + wordmark + pin control */}
        <div
          className="flex items-center px-3 py-4 gap-2"
          style={{ minHeight: 56, borderBottom: "0.5px solid rgba(255,255,255,0.08)" }}
        >
          <AscendMark size={22} />
          {expanded &&
            (brand.logoUrl ? (
              <img
                src={brand.logoUrl}
                alt={brand.name}
                width={110}
                height={32}
                className="flex-1"
                style={{ objectFit: "contain", objectPosition: "left center" }}
              />
            ) : (
              // No logo configured for this tenant — fall back to the live-text
              // lockup so a newly onboarded org still reads as itself.
              <span
                className="flex-1 truncate"
                style={{
                  fontFamily: "var(--2a-font-display)",
                  fontSize: 15,
                  color: "var(--2a-bg)",
                }}
              >
                {brand.name}
              </span>
            ))}
          {expanded && (
            <button
              type="button"
              onClick={togglePin}
              title={pinned ? "Unpin sidebar" : "Pin sidebar open"}
              className="flex items-center justify-center rounded p-1 transition-colors"
              style={{
                color: pinned
                  ? "var(--2a-gold-light)"
                  : "color-mix(in srgb, var(--2a-nav-rest) 60%, transparent)",
                background: "transparent",
              }}
              onMouseEnter={(e) =>
                (e.currentTarget.style.background =
                  "color-mix(in srgb, var(--2a-gold-light) 10%, transparent)")
              }
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <PinIcon pinned={pinned} />
            </button>
          )}
        </div>

        {/* Nav items */}
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.href}
              item={item}
              expanded={expanded}
              active={isActive(item.href)}
              badge={item.href === "/notifications" ? unread : 0}
            />
          ))}

          {can("manage_members") && (
            <>
              {expanded && (
                <div
                  className="px-3 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-wider"
                  style={{
                    color: "color-mix(in srgb, var(--2a-nav-rest) 60%, transparent)",
                  }}
                >
                  Admin
                </div>
              )}
              <NavLink
                item={ADMIN_ITEM}
                expanded={expanded}
                active={isActive(ADMIN_ITEM.href)}
              />
              <NavLink
                item={USERS_ITEM}
                expanded={expanded}
                active={isActive(USERS_ITEM.href)}
              />
            </>
          )}

          {(role === "org_admin" || role === "super_admin") && (
            <NavLink
              item={ORG_SETTINGS_ITEM}
              expanded={expanded}
              active={isActive(ORG_SETTINGS_ITEM.href)}
            />
          )}
          {role === "super_admin" && (
            <NavLink
              item={PLATFORM_ITEM}
              expanded={expanded}
              active={isActive(PLATFORM_ITEM.href)}
            />
          )}
        </nav>
      </div>
    </aside>
  );
}
