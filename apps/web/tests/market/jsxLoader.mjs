/**
 * Node module hooks that let tests import the REAL market components (mkt04a).
 *
 *   resolve: "@/x" -> apps/web/x (the jsconfig.json alias), trying the
 *            extensions Next resolves (.jsx, .js, .mjs) when none is given.
 *   load:    .jsx and apps/web .js files are transpiled with TypeScript's
 *            transpileModule (JSX -> react/jsx-runtime calls, nothing else).
 *
 * TypeScript is already in the repo's node_modules (a transitive dependency of
 * the ESLint toolchain); nothing is added. Registered by render.test.mjs via
 * node:module register().
 */
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import ts from "typescript";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const EXTS = ["", ".jsx", ".js", ".mjs"];

function withExtension(base) {
  for (const ext of EXTS) {
    const p = base + ext;
    if (existsSync(p) && !p.endsWith(path.sep)) return p;
  }
  return null;
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    const hit = withExtension(path.join(WEB, specifier.slice(2)));
    if (hit) return { url: pathToFileURL(hit).href, shortCircuit: true };
  }
  if ((specifier.startsWith("./") || specifier.startsWith("../")) && context.parentURL?.startsWith("file:")) {
    const base = path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier);
    if (!path.extname(base)) {
      const hit = withExtension(base);
      if (hit) return { url: pathToFileURL(hit).href, shortCircuit: true };
    }
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.startsWith("file:")) {
    const file = fileURLToPath(url);
    const inWeb = file.startsWith(WEB + path.sep) && !file.includes(`${path.sep}node_modules${path.sep}`);
    if (file.endsWith(".jsx") || (inWeb && file.endsWith(".js"))) {
      const source = await readFile(file, "utf8");
      const out = ts.transpileModule(source, {
        fileName: file,
        compilerOptions: {
          jsx: ts.JsxEmit.ReactJSX,
          module: ts.ModuleKind.ESNext,
          target: ts.ScriptTarget.ES2022,
        },
      });
      return { format: "module", source: out.outputText, shortCircuit: true };
    }
  }
  return nextLoad(url, context);
}
