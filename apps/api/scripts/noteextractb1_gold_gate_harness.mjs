// noteextractb1 — feeds REAL API envelopes (stdin JSON: {name: payload|null})
// into the gold review screen's own write gate (apps/web/lib/noteGoldGates.mjs)
// and prints {name: canWrite}. Used by verify_noteextractb1.py for the
// client-side proof: the UI renders no write control unless the envelope
// grants it.
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const gate = await import(pathToFileURL(path.resolve(here, "../../web/lib/noteGoldGates.mjs")).href);
const input = JSON.parse(readFileSync(0, "utf8"));
const out = {};
for (const [name, payload] of Object.entries(input)) {
  out[name] = gate.canWriteGold(payload);
}
process.stdout.write(JSON.stringify(out));
