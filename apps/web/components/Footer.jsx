const LINKS = [
  { label: "Disclosures", href: "/disclosures" },
  { label: "Help", href: "/help" },
  { label: "About Us", href: "/about" },
  { label: "Privacy Policy", href: "/privacy" },
];

export default function Footer() {
  return (
    <footer className="border-t border-line bg-sand p-4">
      <nav className="flex flex-wrap items-center justify-center gap-2 text-xs text-muted">
        {LINKS.map((link, index) => (
          <span key={link.href} className="flex items-center gap-2">
            <a href={link.href} className="transition-colors hover:text-ink">
              {link.label}
            </a>
            {index < LINKS.length - 1 && <span aria-hidden="true">·</span>}
          </span>
        ))}
      </nav>
    </footer>
  );
}
