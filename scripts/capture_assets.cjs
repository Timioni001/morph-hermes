/* Capture the catalog card and screenshots from the running MORPH UI.
 *
 * Every image is a real screenshot of the real bundle against running services —
 * the listing should show what a reviewer will actually see. The script leaves one
 * change waiting for approval (proposed through the UI, never applied) so the
 * Approvals view shows what it is for.
 *
 *   node scripts/capture_assets.cjs                       the local harness (make serve)
 *   node scripts/capture_assets.cjs --base http://127.0.0.1:9119 --path /morph \
 *        --hero docs/assets/morph-in-hermes.png           the real Hermes dashboard
 */
const { chromium } = require("playwright");
const path = require("node:path");
const fs = require("node:fs");

const args = process.argv.slice(2);
const flag = (n, d) => { const i = args.indexOf("--" + n); return i >= 0 ? args[i + 1] : d; };
const BASE = flag("base", "http://127.0.0.1:8820");
const PATHNAME = flag("path", "/");
const HERO = flag("hero", "");
const OUT = path.resolve(flag("out", "docs/assets"));
fs.mkdirSync(OUT, { recursive: true });

const VIEWS = ["Workspace", "Connections", "Approvals", "Activity", "Context"];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2
  });
  await page.goto(BASE + PATHNAME, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".morph", { timeout: 25000 });
  // Host chrome that is not part of the product: the harness bar, a host banner.
  await page.addStyleTag({ content: ".harness-bar { display: none !important; }" });
  const dismiss = page.getByRole("button", { name: /^(Close|Dismiss)$/ });
  if (await dismiss.count()) { await dismiss.first().click().catch(() => {}); }

  const open = async (name) => {
    await page.locator(".morph").getByRole("button", { name }).first().click();
    await page.evaluate(() => {
      window.scrollTo(0, 0);
      document.querySelectorAll("*").forEach((el) => { if (el.scrollTop) { el.scrollTop = 0; } });
    });
    await page.waitForFunction(() => !document.querySelector(".mo-toast"), null,
                               { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(700);
  };

  await page.waitForSelector(".morph .mo-metric-value, .morph .mo-empty", { timeout: 25000 });
  if (!(await page.locator(".mo-card", { hasText: "Orders needing attention" }).count())) {
    await page.fill("#mo-goal-input", "Work through delayed orders and the complaints about them");
    await page.getByRole("button", { name: /Transform workspace|Compose workspace/ }).click();
    await page.waitForSelector(".mo-card:has-text('Orders needing attention')", { timeout: 25000 });
  }
  await page.waitForSelector(".morph .mo-metric-value", { timeout: 25000 });
  await page.waitForTimeout(700);

  await open("Approvals");
  if (!(await page.locator(".mo-approval").count())) {
    await open("Workspace");
    const table = page.locator(".mo-card", { hasText: "Orders needing attention" }).first();
    await table.getByRole("button", { name: "Flag for review" }).first().click();
    await page.locator("[role=dialog] textarea").first()
      .fill("Late, and the customer is chasing");
    await page.getByRole("button", { name: "Propose for approval" }).click();
    await page.waitForSelector(".mo-rail-badge", { timeout: 15000 });
  }

  if (HERO) {
    await open("Approvals");
    await page.screenshot({ path: path.resolve(HERO) });
    console.log("captured " + HERO);
    await browser.close();
    return;
  }

  for (const name of VIEWS) {
    await open(name);
    await page.screenshot({ path: path.join(OUT, name.toLowerCase() + ".png") });
    console.log("captured " + name.toLowerCase() + ".png");
  }

  // Catalog card, 2:1: the approval waiting for a person — the point of the plugin.
  await page.setViewportSize({ width: 1200, height: 600 });
  await open("Approvals");
  await page.screenshot({ path: path.join(OUT, "catalog-card.png") });
  console.log("captured catalog-card.png (1200x600 @2x)");
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
