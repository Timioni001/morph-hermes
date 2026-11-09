/* Capture the catalog card and screenshots from the running MORPH UI.
 *
 * Everything here is a real screenshot of the real bundle against the real demo
 * applications — the catalog listing should show what a reviewer will actually see.
 *
 *   node scripts/capture_assets.cjs [--base http://127.0.0.1:8820] [--out docs/assets]
 */
const { chromium } = require("playwright");
const path = require("node:path");
const fs = require("node:fs");

const args = process.argv.slice(2);
const flag = (n, d) => { const i = args.indexOf("--" + n); return i >= 0 ? args[i + 1] : d; };
const BASE = flag("base", "http://127.0.0.1:8820");
const OUT = path.resolve(flag("out", "docs/assets"));
fs.mkdirSync(OUT, { recursive: true });

const VIEWS = [["workspace", 0], ["connections", 1], ["approvals", 2],
               ["activity", 3], ["context", 4]];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2
  });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".morph .mo-metric-value", { timeout: 25000 });
  // The harness chrome is not part of the product; hide it for the listing shots.
  await page.addStyleTag({ content: ".harness-bar { display: none !important; }" });
  await page.waitForTimeout(700);

  for (const [name, index] of VIEWS) {
    await page.locator(".mo-rail-btn").nth(index).click();
    await page.waitForTimeout(650);
    await page.screenshot({ path: path.join(OUT, name + ".png") });
    console.log("captured " + name + ".png");
  }

  // Catalog card: 2:1, the workspace at the top of the page.
  await page.locator(".mo-rail-btn").nth(0).click();
  await page.waitForTimeout(650);
  await page.setViewportSize({ width: 1200, height: 600 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, "catalog-card.png") });
  console.log("captured catalog-card.png (1200x600 @2x)");
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
