"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

export default function DealDetailTabBar({ staff = false }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentTab = searchParams.get("tab") || "overview";

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "documents", label: "Documents" },
    ...(staff ? [{ id: "scoring", label: "Scoring" }] : []),
    ...(staff ? [{ id: "pipeline", label: "Pipeline" }] : []),
  ];

  return (
    <nav className="flex gap-1 border-b border-border">
      {tabs.map((tab) => {
        const isActive = currentTab === tab.id;
        const params = new URLSearchParams(searchParams.toString());
        if (tab.id === "overview") {
          params.delete("tab");
        } else {
          params.set("tab", tab.id);
        }
        const href = params.toString()
          ? `${pathname}?${params.toString()}`
          : pathname;
        return (
          <Link
            key={tab.id}
            href={href}
            className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 ${
              isActive
                ? "border-navy text-navy"
                : "border-transparent text-text-muted hover:text-navy"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
