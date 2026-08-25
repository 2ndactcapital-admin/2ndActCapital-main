/**
 * The single frame every enrollment outcome renders in — valid, expired, spent,
 * revoked, unrecognised, wrong firm.
 *
 * One component on purpose: an outcome cannot accidentally get a different
 * (or missing) presentation, and every state is visibly a deliberate message
 * rather than an error page. Light theme only, per the brand system — cream
 * canvas, white card with a hairline, gold rule, navy primary action, no
 * shadows beyond the 1px hairline, no emoji.
 *
 * A pure presentational server component: it holds no logic about WHICH message
 * to show (that is lib/enrollFlow.mjs, which the verify harness exercises
 * directly) — only how to show it.
 */

const TONE_ACCENT = {
  neutral: "var(--2a-gold, #C5A880)",
  notice: "var(--2a-gold, #C5A880)",
  error: "#9B2335",
};

export default function EnrollShell({
  title,
  body,
  tone = "neutral",
  orgName,
  email,
  actionHref,
  actionLabel,
  footnote,
}) {
  const accent = TONE_ACCENT[tone] || TONE_ACCENT.neutral;

  return (
    <main
      style={{
        minHeight: "100vh",
        backgroundColor: "var(--2a-bg, #FAF9F6)",
        color: "var(--2a-text, #0F172A)",
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
        fontSize: "17px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "520px",
          backgroundColor: "#FFFFFF",
          border: "1px solid #ece8dd",
          borderRadius: "6px",
          padding: "40px",
        }}
      >
        {orgName && (
          <p
            style={{
              margin: "0 0 20px",
              fontSize: "12px",
              fontWeight: 700,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              color: "var(--2a-gold, #C5A880)",
            }}
          >
            {orgName}
          </p>
        )}

        <h1
          style={{
            fontFamily: "'Spectral', Georgia, serif",
            fontWeight: 400,
            fontSize: "30px",
            lineHeight: 1.25,
            margin: "0 0 16px",
            color: "var(--2a-navy, #1B2B4B)",
          }}
        >
          {title}
        </h1>

        <div
          style={{
            width: "48px",
            height: "1px",
            backgroundColor: accent,
            margin: "0 0 20px",
          }}
        />

        {body && (
          <p style={{ margin: "0 0 24px", lineHeight: 1.6, color: "#334155" }}>
            {body}
          </p>
        )}

        {email && (
          <p
            style={{
              margin: "0 0 28px",
              fontSize: "15px",
              color: "#64748B",
              lineHeight: 1.6,
            }}
          >
            This invitation was sent to{" "}
            <span style={{ color: "var(--2a-text, #0F172A)", fontWeight: 600 }}>
              {email}
            </span>
            . Please enrol with that address.
          </p>
        )}

        {actionHref && actionLabel && (
          <a
            href={actionHref}
            style={{
              display: "inline-block",
              padding: "12px 24px",
              backgroundColor: "var(--2a-navy, #1B2B4B)",
              color: "#FFFFFF",
              textDecoration: "none",
              borderRadius: "6px",
              fontWeight: 600,
              fontSize: "16px",
            }}
          >
            {actionLabel}
          </a>
        )}

        {footnote && (
          <p
            style={{
              margin: "24px 0 0",
              fontSize: "14px",
              color: "#64748B",
              lineHeight: 1.6,
            }}
          >
            {footnote}
          </p>
        )}
      </div>
    </main>
  );
}
