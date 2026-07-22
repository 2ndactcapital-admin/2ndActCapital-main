import { Auth0Client } from "@auth0/nextjs-auth0/server";

// Request an access token for the platform API on every login so the
// frontend can call the FastAPI backend on the user's behalf.
export const auth0 = new Auth0Client({
  authorizationParameters: {
    audience: "https://api.2ndactcapital.com",
    scope: "openid profile email",
  },
});
