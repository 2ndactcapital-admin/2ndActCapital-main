import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";

const AscentMark = ({ size = 64 }) => (
  <svg
    viewBox="0 0 512 512"
    width={size}
    height={size}
    aria-hidden="true"
  >
    <rect x="118" y="300" width="80" height="80" rx="20" fill="#C5A880" />
    <rect x="216" y="216" width="80" height="80" rx="20" fill="#C5A880" />
    <rect x="314" y="132" width="80" height="80" rx="20" fill="#E8D5A3" />
  </svg>
);

export default async function MarketingPage() {
  const session = await auth0.getSession();
  if (session) redirect("/dashboard");

  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: "#FAF9F6",
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
        fontSize: "17px",
        color: "#0F172A",
      }}
    >
      {/* Nav */}
      <header
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          zIndex: 50,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "20px 32px",
          backgroundColor: "rgba(250,249,246,0.92)",
          backdropFilter: "blur(8px)",
          borderBottom: "1px solid #E2E8F0",
        }}
      >
        <a href="/" aria-label="2nd Act Capital" style={{ textDecoration: "none" }}>
          <span
            style={{
              fontFamily: "Spectral, Georgia, serif",
              color: "#1B2B4B",
              fontSize: "18px",
              fontWeight: 600,
              letterSpacing: "-0.01em",
            }}
          >
            2nd Act Capital
          </span>
        </a>
        <a
          href="/login"
          style={{ color: "#64748B", fontSize: "14px", fontWeight: 500, textDecoration: "none" }}
        >
          Sign in
        </a>
      </header>

      {/* Hero */}
      <section
        style={{
          backgroundColor: "#1B2B4B",
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          padding: "160px 24px 112px",
        }}
        aria-labelledby="hero-heading"
      >
        <div style={{ marginBottom: "40px" }}>
          <AscentMark size={72} />
        </div>
        <h1
          id="hero-heading"
          style={{
            fontFamily: "Spectral, Georgia, serif",
            fontWeight: 300,
            fontSize: "clamp(2rem, 5vw, 3.25rem)",
            lineHeight: 1.15,
            color: "#FAF9F6",
            letterSpacing: "-0.02em",
            maxWidth: "680px",
            margin: "0 auto 24px",
          }}
        >
          A private community for the{" "}
          <em style={{ fontStyle: "italic", color: "#E8D5A3" }}>post-liquidity</em>{" "}
          life.
        </h1>
        <p
          style={{
            color: "#9AA6BF",
            fontSize: "1.0625rem",
            lineHeight: 1.65,
            maxWidth: "480px",
            margin: "0 auto 40px",
          }}
        >
          For founders and operators who have crossed the threshold — and are
          deciding what comes next. Co-investment, peer counsel, and a private
          AI that learns how you think.
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", justifyContent: "center" }}>
          <a
            href="/login"
            style={{
              display: "inline-block",
              backgroundColor: "#C5A880",
              color: "#1B2B4B",
              borderRadius: "6px",
              padding: "12px 28px",
              fontSize: "14px",
              fontWeight: 600,
              textDecoration: "none",
            }}
          >
            Request an invitation
          </a>
          <a
            href="/login"
            style={{
              display: "inline-block",
              border: "1px solid #C5A880",
              color: "#C5A880",
              borderRadius: "6px",
              padding: "12px 28px",
              fontSize: "14px",
              fontWeight: 500,
              textDecoration: "none",
            }}
          >
            Sign in
          </a>
        </div>
      </section>

      {/* Founding rules */}
      <section style={{ padding: "96px 24px" }} aria-labelledby="rules-heading">
        <div style={{ maxWidth: "896px", margin: "0 auto" }}>
          <p
            style={{
              fontSize: "12px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.22em",
              color: "#C5A880",
              textAlign: "center",
              marginBottom: "48px",
            }}
          >
            Founding rules
          </p>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "24px",
            }}
          >
            {[
              {
                num: "01",
                title: "Sponsored entry only.",
                body: "Every member is vouched for by someone already inside. No applications. No pitch decks. The community self-selects.",
              },
              {
                num: "02",
                title: "Bring value beyond capital.",
                body: "Members who contribute experience, introductions, and honest counsel are what make this worth belonging to.",
              },
              {
                num: "03",
                title: "No assholes.",
                body: "One complaint upheld by two members ends a membership. Quietly. No drama, no exceptions.",
              },
            ].map(({ num, title, body }) => (
              <div
                key={num}
                style={{
                  backgroundColor: "#FFFFFF",
                  border: "1px solid #ece8dd",
                  borderRadius: "8px",
                  padding: "24px",
                }}
              >
                <p
                  style={{
                    fontSize: "12px",
                    fontWeight: 700,
                    color: "#C5A880",
                    letterSpacing: "0.1em",
                    marginBottom: "12px",
                  }}
                >
                  {num}
                </p>
                <h3
                  style={{
                    fontFamily: "Spectral, Georgia, serif",
                    fontWeight: 500,
                    fontSize: "1.0625rem",
                    color: "#1B2B4B",
                    marginBottom: "8px",
                  }}
                >
                  {title}
                </h3>
                <p style={{ color: "#64748B", fontSize: "0.9375rem", lineHeight: 1.6, margin: 0 }}>
                  {body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Three pillars */}
      <section
        style={{ backgroundColor: "#F5F1EB", padding: "96px 24px" }}
        aria-labelledby="pillars-heading"
      >
        <div style={{ maxWidth: "896px", margin: "0 auto" }}>
          <h2
            id="pillars-heading"
            style={{
              fontFamily: "Spectral, Georgia, serif",
              fontWeight: 300,
              fontSize: "clamp(1.5rem, 3vw, 2.125rem)",
              color: "#1B2B4B",
              letterSpacing: "-0.015em",
              textAlign: "center",
              marginBottom: "16px",
            }}
          >
            What membership includes
          </h2>
          <p style={{ color: "#64748B", textAlign: "center", marginBottom: "64px" }}>
            Three things we built because they did not exist anywhere else.
          </p>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "24px",
            }}
          >
            {[
              {
                label: "Peer community",
                body: "A private network of people who have been exactly where you are. No strangers. No noise. Conversations worth having.",
              },
              {
                label: "Co-investment access",
                body: "Curated deals sourced and diligenced by members. Participate at your level, on your timeline, alongside people you trust.",
              },
              {
                label: "A private AI advisor",
                body: "Your personal AI that learns your priorities, surfaces relevant deals, and helps you think — without judgment, at any hour.",
              },
            ].map(({ label, body }) => (
              <div
                key={label}
                style={{
                  backgroundColor: "#FFFFFF",
                  border: "1px solid #ece8dd",
                  borderRadius: "8px",
                  padding: "24px",
                }}
              >
                <div
                  aria-hidden="true"
                  style={{
                    width: "4px",
                    height: "32px",
                    backgroundColor: "#C5A880",
                    borderRadius: "2px",
                    marginBottom: "20px",
                  }}
                />
                <h3
                  style={{
                    fontFamily: "Spectral, Georgia, serif",
                    fontWeight: 500,
                    fontSize: "1.0625rem",
                    color: "#1B2B4B",
                    marginBottom: "8px",
                  }}
                >
                  {label}
                </h3>
                <p style={{ color: "#64748B", fontSize: "0.9375rem", lineHeight: 1.6, margin: 0 }}>
                  {body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Assistant band */}
      <section
        style={{ backgroundColor: "#1B2B4B", padding: "96px 24px" }}
        aria-labelledby="assistant-heading"
      >
        <div style={{ maxWidth: "640px", margin: "0 auto", textAlign: "center" }}>
          <p
            style={{
              fontSize: "12px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.22em",
              color: "#C5A880",
              marginBottom: "24px",
            }}
          >
            Your private AI
          </p>
          <h2
            id="assistant-heading"
            style={{
              fontFamily: "Spectral, Georgia, serif",
              fontWeight: 300,
              fontSize: "clamp(1.5rem, 3vw, 2.125rem)",
              color: "#FAF9F6",
              letterSpacing: "-0.015em",
              marginBottom: "24px",
            }}
          >
            The first thing it asks is what you would like to call it.
          </h2>
          <p style={{ color: "#9AA6BF", lineHeight: 1.7, margin: 0 }}>
            Not your name. Its name. A small question that sets the tone: this
            is yours, shaped around how you think. It reads the deals you care
            about, remembers what matters, and surfaces connections you would
            have missed — quietly, without fanfare.
          </p>
        </div>
      </section>

      {/* Co-investment */}
      <section style={{ padding: "96px 24px" }} aria-labelledby="coinvest-heading">
        <div
          style={{
            maxWidth: "896px",
            margin: "0 auto",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
            gap: "48px",
            alignItems: "center",
          }}
        >
          <div>
            <p
              style={{
                fontSize: "12px",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.22em",
                color: "#C5A880",
                marginBottom: "16px",
              }}
            >
              Co-investment
            </p>
            <h2
              id="coinvest-heading"
              style={{
                fontFamily: "Spectral, Georgia, serif",
                fontWeight: 300,
                fontSize: "clamp(1.5rem, 3vw, 2rem)",
                color: "#1B2B4B",
                letterSpacing: "-0.015em",
                marginBottom: "20px",
              }}
            >
              Deals worth your attention, not just your capital.
            </h2>
            <p style={{ color: "#64748B", lineHeight: 1.7, margin: 0 }}>
              Every opportunity on the platform has been evaluated by at least
              one member who has operational skin in the game. You see the
              thesis, the diligence, and the members backing it — before you
              decide anything.
            </p>
          </div>
          <div
            style={{
              backgroundColor: "#FFFFFF",
              border: "1px solid #ece8dd",
              borderRadius: "8px",
              padding: "32px",
            }}
          >
            <dl>
              {[
                { label: "Deal visibility", value: "Members only" },
                { label: "Diligence standard", value: "Peer-reviewed" },
                { label: "Minimum check", value: "Set per SPV" },
                { label: "Carry", value: "Disclosed upfront" },
              ].map(({ label, value }, idx, arr) => (
                <div
                  key={label}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    paddingBottom: "16px",
                    marginBottom: idx < arr.length - 1 ? "16px" : 0,
                    borderBottom: idx < arr.length - 1 ? "1px solid #ece8dd" : "none",
                  }}
                >
                  <dt
                    style={{
                      fontSize: "11px",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.08em",
                      color: "#64748B",
                    }}
                  >
                    {label}
                  </dt>
                  <dd style={{ fontSize: "14px", fontWeight: 500, color: "#1B2B4B", margin: 0 }}>
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </section>

      {/* Enroll CTA */}
      <section
        style={{ backgroundColor: "#F5F1EB", padding: "112px 24px", textAlign: "center" }}
        aria-labelledby="cta-heading"
      >
        <div style={{ maxWidth: "560px", margin: "0 auto" }}>
          <AscentMark size={40} />
          <h2
            id="cta-heading"
            style={{
              fontFamily: "Spectral, Georgia, serif",
              fontWeight: 300,
              fontSize: "clamp(1.5rem, 3vw, 2.25rem)",
              color: "#1B2B4B",
              letterSpacing: "-0.015em",
              marginTop: "32px",
              marginBottom: "16px",
            }}
          >
            Let&rsquo;s begin with a conversation.
            <br />
            <em style={{ fontStyle: "italic" }}>No forms.</em>
          </h2>
          <p style={{ color: "#64748B", lineHeight: 1.7, marginBottom: "40px" }}>
            If someone you trust has pointed you here, you are already most of
            the way in. The rest is a conversation.
          </p>
          <a
            href="/login"
            style={{
              display: "inline-block",
              backgroundColor: "#1B2B4B",
              color: "#FAF9F6",
              borderRadius: "6px",
              padding: "14px 32px",
              fontSize: "14px",
              fontWeight: 600,
              textDecoration: "none",
            }}
          >
            Request an invitation
          </a>
        </div>
      </section>

      {/* Footer */}
      <footer
        style={{
          backgroundColor: "#1B2B4B",
          borderTop: "1px solid rgba(197,168,128,0.15)",
          padding: "40px 32px",
        }}
      >
        <div
          style={{
            maxWidth: "896px",
            margin: "0 auto",
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "16px",
          }}
        >
          <span
            style={{
              fontFamily: "Spectral, Georgia, serif",
              color: "#9AA6BF",
              fontSize: "15px",
            }}
          >
            2nd Act Capital
          </span>
          <p
            style={{
              color: "#64748B",
              fontSize: "13px",
              lineHeight: 1.6,
              textAlign: "center",
              margin: 0,
            }}
          >
            Discretion is the foundation. Membership is private. Information shared on the platform stays on the platform.
          </p>
          <a
            href="/login"
            style={{ color: "#9AA6BF", fontSize: "14px", textDecoration: "none", whiteSpace: "nowrap" }}
          >
            Sign in
          </a>
        </div>
      </footer>

      <style>{`
        @media (prefers-reduced-motion: reduce) {
          * { transition: none !important; animation: none !important; }
        }
        a:focus-visible {
          outline: 2px solid #C5A880;
          outline-offset: 3px;
          border-radius: 3px;
        }
      `}</style>
    </div>
  );
}
