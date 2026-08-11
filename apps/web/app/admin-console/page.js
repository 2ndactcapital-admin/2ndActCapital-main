import { headers } from "next/headers";
import { redirect } from "next/navigation";
import {
  getAuthClientForHost,
  isHollisworksAdminHost,
} from "@/lib/authForHost";

export const metadata = {
  title: "Hollisworks — Platform Admin",
};

/**
 * admin.hollisworks.com landing (the Hollisworks-tenant callback destination).
 *
 * Reads the session from the HOLLISWORKS client (host-aware) — never the 2nd Act
 * client — so the identity shown here is the one just authenticated against the
 * Hollisworks tenant. When a member of that tenant reaches the FastAPI backend,
 * their token's issuer maps them to Super Admin (see apps/api/main.py +
 * services/users.py). This page is the human-visible confirmation of that login.
 */
export default async function AdminConsolePage() {
  const host = (await headers()).get("host") || "";

  // This surface is only meaningful on the Hollisworks admin host. Anywhere else
  // there is no Hollisworks session to read — send the visitor to normal login.
  if (!isHollisworksAdminHost(host)) redirect("/login");

  const client = getAuthClientForHost(host);
  const session = await client.getSession();
  if (!session) redirect("/login");

  const user = session.user || {};
  const name = user.name || user.nickname || user.email || "Platform staff";

  return (
    <div
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
          maxWidth: 520,
          backgroundColor: "#fff",
          border: "1px solid #ece8dd",
          borderRadius: 6,
          boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
          padding: "40px 40px 36px",
        }}
      >
        <div
          style={{
            fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
            fontWeight: 700,
            fontSize: 12,
            letterSpacing: "0.22em",
            textTransform: "uppercase",
            color: "var(--2a-gold, #C5A880)",
            marginBottom: 14,
          }}
        >
          Hollisworks · Platform Admin
        </div>
        <h1
          style={{
            fontFamily: "'Spectral', Georgia, serif",
            fontWeight: 500,
            fontSize: 30,
            lineHeight: 1.15,
            margin: "0 0 12px",
            color: "var(--2a-navy, #1B2B4B)",
          }}
        >
          Signed in as platform staff
        </h1>
        <p style={{ margin: "0 0 8px", color: "#334155" }}>{name}</p>
        <p style={{ margin: "0 0 28px", color: "#64748B", fontSize: 15 }}>
          Authenticated against the Hollisworks tenant. Your access is Super
          Admin across every client org.
        </p>
        <a
          href="/auth/logout"
          style={{
            display: "inline-block",
            backgroundColor: "var(--2a-navy, #1B2B4B)",
            color: "#fff",
            textDecoration: "none",
            padding: "11px 20px",
            borderRadius: 6,
            fontWeight: 600,
            fontSize: 15,
          }}
        >
          Sign out
        </a>
      </div>
    </div>
  );
}
