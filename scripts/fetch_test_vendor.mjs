/* Fetch the React UMD builds the browser test harness needs.
 *
 * MORPH itself ships no JavaScript dependencies: the Hermes dashboard provides
 * React through its plugin SDK. The harness in tests/e2e has to stand in for that
 * dashboard, so it needs a React of its own. It is downloaded here, into a
 * gitignored folder, and is test-only.
 *
 *   node scripts/fetch_test_vendor.mjs
 */
import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "tests", "e2e", "harness", "vendor");
const files = [
  ["react.production.min.js",
   "https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js"],
  ["react-dom.production.min.js",
   "https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js"],
];

mkdirSync(out, { recursive: true });
for (const [name, url] of files) {
  const path = join(out, name);
  if (existsSync(path)) { console.log(`have ${name}`); continue; }
  const response = await fetch(url);
  if (!response.ok) { throw new Error(`${url} -> HTTP ${response.status}`); }
  writeFileSync(path, Buffer.from(await response.arrayBuffer()));
  console.log(`fetched ${name}`);
}
console.log("harness vendor ready");
