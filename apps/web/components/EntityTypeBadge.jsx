import { typeLabel } from "@/lib/entityTypes";

// Gold badge (#E8D5A3 background, navy text) for an entity type.
export default function EntityTypeBadge({ type }) {
  return (
    <span className="inline-flex items-center rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy">
      {typeLabel(type)}
    </span>
  );
}
