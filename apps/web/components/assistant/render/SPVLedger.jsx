"use client";

const fmt = (value) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value ?? 0);

const netColor = (value) => {
  if (value > 0) return "#2D6A4F";
  if (value < 0) return "#9B2335";
  return "#64748B";
};

export default function SPVLedger({ spv_id, spv_name, summary = {} }) {
  const { total_called, total_distributed, total_fees, net } = summary;

  const metrics = [
    { label: "Total Called", value: total_called },
    { label: "Total Distributed", value: total_distributed },
    { label: "Total Fees", value: total_fees },
    { label: "Net", value: net, isNet: true },
  ];

  return (
    <div
      style={{
        background: "#FFFFFF",
        border: "1px solid #ece8dd",
        borderRadius: 6,
        padding: "20px 24px",
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
      }}
    >
      <h3
        style={{
          fontFamily: "'Spectral', Georgia, serif",
          fontWeight: 300,
          fontSize: 20,
          color: "#1B2B4B",
          margin: "0 0 20px 0",
          lineHeight: 1.3,
        }}
      >
        {spv_name}
      </h3>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "16px 24px",
          marginBottom: 20,
        }}
      >
        {metrics.map(({ label, value, isNet }) => (
          <div key={label}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.22em",
                color: "#C5A880",
                marginBottom: 4,
              }}
            >
              {label}
            </div>
            <div
              style={{
                fontSize: 16,
                fontWeight: 500,
                color: isNet ? netColor(value) : "#0F172A",
                textAlign: "right",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {fmt(value)}
            </div>
          </div>
        ))}
      </div>

      <div style={{ borderTop: "1px solid #ece8dd", paddingTop: 16 }}>
        <a
          href={`/spvs/${spv_id}?tab=transactions`}
          style={{
            display: "inline-block",
            padding: "8px 18px",
            background: "#1B2B4B",
            color: "#FAF9F6",
            borderRadius: 4,
            fontSize: 14,
            fontWeight: 500,
            textDecoration: "none",
            letterSpacing: "0.01em",
          }}
        >
          Open Ledger
        </a>
      </div>
    </div>
  );
}
