"use client";

import { useTheme } from "@/components/ThemeProvider";

// Sprint 24: footer destinations come from the tenant's org_settings
// (footer.privacy_url / footer.terms_url / footer.support_email) rather than a
// hardcoded list. Entries with no configured value are omitted entirely.
const STATIC_LINKS = [
  { label: "Disclosures", href: "/disclosures" },
  { label: "Help", href: "/help" },
  { label: "About Us", href: "/about" },
];

export default function Footer() {
  const { footer } = useTheme();

  const links = [
    ...STATIC_LINKS,
    footer.termsUrl && { label: "Terms", href: footer.termsUrl },
    footer.privacyUrl && { label: "Privacy Policy", href: footer.privacyUrl },
    footer.supportEmail && {
      label: "Support",
      href: `mailto:${footer.supportEmail}`,
    },
  ].filter(Boolean);

  return (
    <footer className="border-t-[0.5px] border-border bg-bg-sidebar p-4">
      <nav className="flex flex-wrap items-center justify-center gap-2 text-xs text-text-muted">
        {links.map((link, index) => (
          <span key={link.href} className="flex items-center gap-2">
            <a href={link.href} className="transition-colors hover:text-text-primary">
              {link.label}
            </a>
            {index < links.length - 1 && <span aria-hidden="true">·</span>}
          </span>
        ))}
      </nav>
    </footer>
  );
}
