import { Auth0Client } from "@auth0/nextjs-auth0/server";
import { twoActAppBaseUrls } from "@/lib/authHostConfig";

// Request an access token for the platform API on every login so the
// frontend can call the FastAPI backend on the user's behalf.
export const auth0 = new Auth0Client({
  // Host-derived callback base URL. Previously this client passed NO appBaseUrl,
  // so the SDK fell back to the STATIC `process.env.APP_BASE_URL`
  // (https://2ndactcapital.com) and `resolveAppBaseUrl` returned that string
  // verbatim without ever reading the request — every host this client serves
  // got the bare domain in `redirect_uri`. A signup begun on
  // 2ndactcapital.hollisworks.com therefore came back to
  // https://2ndactcapital.com/auth/callback, where the transaction cookie it had
  // just written was not visible: "the state parameter is invalid".
  //
  // Passing an ALLOW-LIST ARRAY is the same fix already proven for the
  // Hollisworks client (auth0Hollisworks.js): the SDK infers the base from the
  // REAL request Host, validates it against the list, and throws on anything
  // unlisted rather than silently using another host's domain. The list is built
  // by `twoActAppBaseUrls` and still contains APP_BASE_URL, so a request from
  // 2ndactcapital.com resolves to https://2ndactcapital.com exactly as before.
  appBaseUrl: twoActAppBaseUrls(),
  authorizationParameters: {
    audience: "https://api.2ndactcapital.com",
    scope: "openid profile email",
  },
});
