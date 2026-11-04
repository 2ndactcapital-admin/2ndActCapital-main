import Link from "next/link";

// PERMS1 (WF-04) — the app's own not-found page (was Next.js's bare default):
// same light/cream chrome, a way back, no dead end.
export default function NotFound() {
  return (
    <main className="min-h-screen bg-[#FAF9F6] px-6 py-24">
      <div className="mx-auto max-w-xl rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center">
        <h1 className="font-[Spectral,Georgia,serif] text-3xl text-[#1B2B4B]">This page does not exist</h1>
        <p className="mt-3 text-[#64748B]">
          The address may have changed, or the page may not be open to your account.
        </p>
        <Link href="/dashboard" className="mt-6 inline-block text-[#1B2B4B] underline">
          Return to your dashboard
        </Link>
      </div>
    </main>
  );
}
