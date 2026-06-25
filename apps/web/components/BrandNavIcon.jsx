const ICONS = {
  dashboard: (
    <>
      <rect x="3" y="3" width="7" height="8.5" rx="1.5" />
      <rect x="3" y="15.5" width="7" height="5.5" rx="1.5" />
      <rect x="14" y="3" width="7" height="5.5" rx="1.5" />
      <rect x="14" y="10" width="7" height="11" rx="1.5" />
    </>
  ),
  marketplace: (
    <>
      <path d="M4 9.5 L5.2 4.5 H18.8 L20 9.5" />
      <path d="M4 9.5 c1.3 1.6 3.1 1.6 4 0 c.9 1.6 3.1 1.6 4 0 c.9 1.6 3.1 1.6 4 0 c.9 1.6 2.7 1.6 4 0" />
      <path d="M5.5 11.5 V20 H18.5 V11.5" />
      <path d="M10 20 V14.5 H14 V20" />
    </>
  ),
  portfolio: (
    <>
      <rect x="3" y="7.5" width="18" height="12.5" rx="2.2" />
      <path d="M8.5 7.5 V6 a2 2 0 0 1 2-2 h3 a2 2 0 0 1 2 2 v1.5" />
      <path d="M3 13 h18" />
      <path d="M11 13 v2 h2 v-2" />
    </>
  ),
  "portfolio-reporting": (
    <>
      <path d="M6 3 h7 l5 5 v11 a2 2 0 0 1-2 2 H6 a2 2 0 0 1-2-2 V5 a2 2 0 0 1 2-2 Z" />
      <path d="M13 3 v5 h5" />
      <path d="M8 17 l2.4-3 l2 1.8 l3-4" />
    </>
  ),
  "spv-manager": (
    <>
      <path d="M5 3.5 h8.5 L18 8 V20.5 a1.5 1.5 0 0 1-1.5 1.5 H5 a1.5 1.5 0 0 1-1.5-1.5 V5 a1.5 1.5 0 0 1 1.5-1.5 Z" />
      <path d="M13.5 3.5 V8 H18" />
      <path d="M6.6 11 h6.8" />
      <path d="M6.6 13.4 h4.4" />
      <circle cx="9.5" cy="17.2" r="2.2" />
      <path d="M7.9 18.9 L7.2 21.2 9.5 20.1 11.8 21.2 11.1 18.9" />
    </>
  ),
  "investment-profile": (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2.2" />
      <circle cx="9" cy="11" r="2.3" />
      <path d="M5.5 16.5 a3.6 3.4 0 0 1 7 0" />
      <path d="M15 10 h3.5" />
      <path d="M15 13.5 h2.5" />
    </>
  ),
  insurance: (
    <>
      <path d="M12 3 l7.5 3 v5.2 c0 4.8-3.3 8-7.5 9.6 C7.8 19.2 4.5 16 4.5 11.2 V6 Z" />
      <path d="M9 12 l2 2 l4-4.2" />
    </>
  ),
  community: (
    <>
      <circle cx="9" cy="9" r="3" />
      <path d="M3.5 19.5 a5.6 5 0 0 1 11 0" />
      <circle cx="17" cy="10.5" r="2.4" />
      <path d="M15.2 19.5 a4.6 4 0 0 1 6.3-4.1" />
    </>
  ),
  admin: (
    <>
      <path d="M4 7 H20" />
      <path d="M4 12 H20" />
      <path d="M4 17 H20" />
      <circle cx="9" cy="7" r="2.1" fill="var(--2a-navy)" />
      <circle cx="15" cy="12" r="2.1" fill="var(--2a-navy)" />
      <circle cx="8" cy="17" r="2.1" fill="var(--2a-navy)" />
    </>
  ),
  notifications: (
    <>
      <path d="M6 9 a6 6 0 0 1 12 0 c0 5 1.5 6.5 2.5 7.5 H3.5 c1-1 2.5-2.5 2.5-7.5" />
      <path d="M10 20 a2 2 0 0 0 4 0" />
    </>
  ),
};

export default function BrandNavIcon({ name, size = 20, className = "", style }) {
  const paths = ICONS[name];
  if (!paths) return null;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      {paths}
    </svg>
  );
}
