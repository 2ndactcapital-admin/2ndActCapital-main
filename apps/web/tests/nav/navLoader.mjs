/**
 * navregroup — extra resolve hooks for the nav tests, registered AFTER
 * tests/market/jsxLoader.mjs (so they run first):
 *   - bare "next/<name>" (navigation, link, headers…) → its .js file (Next's package has
 *     no "exports" map for plain Node ESM);
 *   - when NAV_TEST_STUBS=1, "@/lib/authServer" and "@/lib/api" resolve to
 *     tests/nav/stubs/*.mjs, so a server component (GroupLanding) can run in
 *     Node with a fed session and /users/me payload. Nothing else is stubbed:
 *     lib/menuVisibility.mjs and the components are the real modules.
 */
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STUBS = {
  "@/lib/authServer": path.join(HERE, "stubs", "authServer.mjs"),
  "@/lib/api": path.join(HERE, "stubs", "api.mjs"),
};

export async function resolve(specifier, context, nextResolve) {
  if (/^next\/[a-z-]+$/.test(specifier)) {
    return nextResolve(`${specifier}.js`, context);
  }
  if (process.env.NAV_TEST_STUBS === "1" && STUBS[specifier]) {
    return { url: pathToFileURL(STUBS[specifier]).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
