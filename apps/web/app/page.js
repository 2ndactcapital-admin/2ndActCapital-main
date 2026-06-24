import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";

export default async function Home() {
  // Members go straight to their dashboard.
  const session = await auth0.getSession();
  if (session) {
    redirect("/dashboard");
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-canvas px-6 text-center">
      <div className="mb-4 h-12 w-12 rounded-md bg-navy" aria-hidden="true" />
      <h1 className="text-5xl font-semibold tracking-tight text-navy">
        2nd Act Capital
      </h1>
      <p className="mt-4 text-lg text-ink-soft">
        A private community for the post-liquidity investor
      </p>
      <a
        href="/auth/login?returnTo=/dashboard"
        className="mt-10 rounded-md bg-navy px-8 py-3 text-base font-medium text-white transition-opacity hover:opacity-90"
      >
        Login
      </a>
    </main>
  );
}
