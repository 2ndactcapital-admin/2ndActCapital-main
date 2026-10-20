"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { usePermissions } from "@/lib/usePermissions";
import { tabForPath, visibleTabs } from "@/lib/menuVisibility";

/**
 * The tab bar of a master page (navregroup). Rendered by AppShell above every
 * page, it shows only when the current path is a tab of a group with more than
 * one visible tab. Each tab stays its own route and its own page code; the tabs,
 * their labels, order and gates all come from lib/menuVisibility.mjs.
 *
 * Exported pure model so node tests can render it from a /users/me payload.
 */
export function tabBarModel(me, pathname) {
  const hit = tabForPath(pathname);
  if (!hit) return null;
  const tabs = visibleTabs(me, hit.group.id);
  if (tabs.length < 2) return null;
  return {
    groupId: hit.group.id,
    groupLabel: hit.group.label,
    tabs: tabs.map((t) => ({ href: t.href, label: t.label, active: t.href === hit.item.href })),
  };
}

export function TabBar({ model }) {
  if (!model) return null;
  return (
    <nav
      aria-label={model.groupLabel}
      className="-mt-2 mb-6 flex flex-wrap gap-1 border-b border-border"
    >
      {model.tabs.map((tab) => (
        <Link
          key={tab.href}
          href={tab.href}
          aria-current={tab.active ? "page" : undefined}
          className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
            tab.active
              ? "border-gold text-navy"
              : "border-transparent text-text-muted hover:text-navy"
          }`}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}

export default function TabbedSection() {
  const pathname = usePathname();
  const { me } = usePermissions();
  return <TabBar model={tabBarModel(me, pathname)} />;
}
