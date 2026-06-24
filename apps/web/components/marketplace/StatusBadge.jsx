import { humanize } from "@/lib/format";

// Status -> Tailwind-safe badge colors.
const STATUS_COLORS = {
  active: "bg-[#DCFCE7] text-[#166534]",
  under_review: "bg-[#DBEAFE] text-[#1E40AF]",
  submitted: "bg-gold-light text-navy",
  draft: "bg-border text-text-secondary",
  closed: "bg-[#FBE3E6] text-[#9B2335]",
  archived: "bg-border text-text-muted",
};

export default function StatusBadge({ status }) {
  const color = STATUS_COLORS[status] || "bg-border text-text-secondary";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${color}`}
    >
      {humanize(status)}
    </span>
  );
}
