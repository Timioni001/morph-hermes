/* Record the MORPH demonstration in a real browser.
 *
 * Everything on screen is a real interaction with the real plugin bundle, the real
 * dashboard API and the three real applications. Nothing is staged: the approval
 * really applies, and the source application's own page is reloaded afterwards to
 * show the change. The only thing added in post is the caption text, whose timings
 * this script records as it goes.
 *
 *   node scripts/record_demo.cjs [--base http://127.0.0.1:8820] [--out .media]
 */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf("--" + name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const BASE = flag("base", "http://127.0.0.1:8820");
const STOREFRONT = flag("storefront", "http://127.0.0.1:8811");
const OUT = path.resolve(flag("out", ".media"));
const WIDTH = 1600;
const HEIGHT = 900;

fs.mkdirSync(OUT, { recursive: true });

const captions = [];
let started = 0;
let current = null;

function now() { return (Date.now() - started) / 1000; }

function say(text) {
  if (current) { current.end = now(); captions.push(current); }
  current = { start: now(), end: null, text };
}

function closeCaption() {
  if (current) { current.end = now(); captions.push(current); current = null; }
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 1,
    recordVideo: { dir: OUT, size: { width: WIDTH, height: HEIGHT } },
    reducedMotion: "no-preference",
  });
  const page = await context.newPage();
  started = Date.now();

  // 1 ─ the source application, before anything happens
  say("Three independent applications. This is the Storefront's own record list.");
  await page.goto(STOREFRONT + "/", { waitUntil: "networkidle" });
  await wait(2200);
  await page.evaluate(() => {
    const row = Array.from(document.querySelectorAll("tr"))
      .find((r) => r.textContent.includes("ORD-2045"));
    if (row) { row.scrollIntoView({ block: "center", behavior: "smooth" }); }
  });
  await wait(2600);

  // 2 ─ MORPH, empty-handed
  say("MORPH is connected to all three. It has no layouts of its own.");
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".mo-metric-value", { timeout: 30000 });
  await wait(1800);

  // 3 ─ a goal, typed
  say("A goal, in plain language.");
  const goal = "Work through delayed orders and the complaints about them";
  await page.click("#mo-goal-input");
  for (const character of goal) {
    await page.keyboard.type(character, { delay: 18 });
  }
  await wait(600);

  say("MORPH reads the three schemas and composes an interface from them.");
  await page.getByRole("button", { name: /Transform workspace|Compose workspace/ }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll(".mo-toast-title"))
    .some((n) => /Workspace transformed/.test(n.textContent)), null, { timeout: 30000 });
  await page.waitForSelector(".mo-metric-value");
  await wait(2000);

  say("Live counts, a chart, the queue — all read from the applications just now.");
  await page.mouse.wheel(0, 420);
  await wait(2400);

  say("And a join across two applications: orders beside their support cases.");
  await page.mouse.wheel(0, 560);
  await wait(2800);

  // 4 ─ select a record
  say("Choosing a row retargets the detail pane and the action form.");
  const table = page.locator(".mo-card", { hasText: "Orders needing attention" }).first();
  await table.scrollIntoViewIfNeeded();
  await wait(700);
  const row = table.locator("tbody tr", { hasText: "ORD-2045" });
  await row.hover();
  await wait(500);
  await row.click();
  await wait(2200);

  // 5 ─ propose
  say("The action is proposed, never applied. Nothing has changed yet.");
  await row.getByRole("button", { name: "Flag for review" }).click();
  await page.waitForSelector("[role=dialog]");
  await wait(900);
  await page.locator("[role=dialog] textarea").first()
    .type("75 hours late and the customer is chasing", { delay: 16 });
  await wait(700);
  await page.getByRole("button", { name: "Propose for approval" }).click();
  await page.waitForSelector(".mo-approval");
  await wait(1200);

  // 6 ─ approve
  say("A human approves. The agent cannot: it is never given the token.");
  const card = page.locator(".mo-approval", { hasText: "flag for review" }).first();
  await card.scrollIntoViewIfNeeded();
  await wait(1400);
  await card.getByRole("button", { name: "Approve & apply" }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll(".mo-toast-title"))
    .some((n) => /Applied and verified/.test(n.textContent)), null, { timeout: 30000 });
  say("Applied, then verified by re-reading the record from the Storefront.");
  await wait(2800);

  // 7 ─ the proof, in the source application
  say("The proof: the Storefront's own page, reloaded.");
  await page.goto(STOREFRONT + "/", { waitUntil: "networkidle" });
  await page.evaluate(() => {
    const target = Array.from(document.querySelectorAll("tr"))
      .find((r) => r.textContent.includes("ORD-2045"));
    if (target) {
      target.scrollIntoView({ block: "center" });
      target.style.transition = "background .4s ease";
      target.style.background = "#eef2ff";
    }
  });
  await wait(3600);

  // 8 ─ the goal changes
  say("Change the goal and the workspace changes with it.");
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".mo-metric-value", { timeout: 30000 });
  await wait(1200);
  await page.click("#mo-goal-input");
  for (const character of "Stock running low and the restocks I should request") {
    await page.keyboard.type(character, { delay: 16 });
  }
  await page.getByRole("button", { name: /Transform workspace/ }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll(".mo-toast-title"))
    .some((n) => /Workspace transformed/.test(n.textContent)), null, { timeout: 30000 });
  await page.waitForSelector(".mo-metric-value");
  say("Same applications, a different interface — and your selection carried over.");
  await wait(2600);
  await page.mouse.wheel(0, 500);
  await wait(2400);

  say("MORPH. The agent proposes, you approve, the change is verified.");
  await wait(2400);

  closeCaption();
  const video = page.video();
  await context.close();
  await browser.close();

  const raw = await video.path();
  const target = path.join(OUT, "raw.webm");
  fs.renameSync(raw, target);
  fs.writeFileSync(path.join(OUT, "captions.json"),
                   JSON.stringify({ width: WIDTH, height: HEIGHT, captions }, null, 2));
  console.log(`recorded ${target} (${captions.length} captions, ` +
              `${captions[captions.length - 1].end.toFixed(1)}s of interaction)`);
}

main().catch((error) => { console.error(error); process.exit(1); });
