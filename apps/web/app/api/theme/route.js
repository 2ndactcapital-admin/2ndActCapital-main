import { forwardToApi } from "@/lib/apiForward";

// Client-side theme fetch (Rule 5: the browser talks to Next, Next talks to
// FastAPI). The root layout loads the theme server-side; this route exists for
// client components that need to re-read it after an admin saves settings.
export async function GET() {
  return forwardToApi("/api/v1/theme");
}
