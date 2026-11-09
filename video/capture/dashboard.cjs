/* Record the real MORPH tab inside the real Hermes dashboard, connected to a real
 * GitHub repository. Every click is a real interaction; approvals really write to
 * GitHub. Timings of each beat are written beside the video for the edit.
 *
 *   node video/capture/dashboard.cjs --out <dir> [--rehearse]   (rehearse: no approvals)
 */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
const flag = (n, d) => { const i = args.indexOf("--" + n); return i >= 0 ? (args[i + 1] || true) : d; };
const OUT = path.resolve(flag("out", "video/footage"));
const REHEARSE = args.includes("--rehearse");
const BASE = "http://127.0.0.1:9119/morph";
fs.mkdirSync(OUT, { recursive: true });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const beats = [];
let t0 = 0;
const beat = (name) => { beats.push({ name, t: (Date.now() - t0) / 1000 }); console.log(name, beats.at(-1).t.toFixed(1)); };

const CURSOR = `
  const c = document.createElement("div"); c.id = "__cursor";
  c.style.cssText = "position:fixed;z-index:2147483647;width:22px;height:22px;border-radius:50%;" +
    "background:rgba(59,130,246,.28);border:2px solid #3b82f6;pointer-events:none;" +
    "transform:translate(-50%,-50%);transition:width .12s,height .12s,background .12s;left:-50px;top:-50px";
  document.documentElement.appendChild(c);
  document.addEventListener("mousemove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
  document.addEventListener("mousedown", () => { c.style.width = "14px"; c.style.height = "14px"; c.style.background = "rgba(59,130,246,.6)"; }, true);
  document.addEventListener("mouseup", () => { c.style.width = "22px"; c.style.height = "22px"; c.style.background = "rgba(59,130,246,.28)"; }, true);
`;

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1,
    recordVideo: { dir: OUT, size: { width: 1920, height: 1080 } },
  });
  const page = await context.newPage();
  t0 = Date.now();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForSelector(".morph", { timeout: 30000 });
  const x = page.getByRole("button", { name: /^(Close|Dismiss)$/ });
  if (await x.count()) { await x.first().click().catch(() => {}); }
  await page.evaluate(CURSOR);
  const morph = page.locator(".morph");
  const glide = async (locator) => {
    const box = await locator.boundingBox();
    if (box) { await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 28 }); await wait(250); }
  };
  const press = async (locator) => { await locator.scrollIntoViewIfNeeded(); await glide(locator); await locator.click(); };
  const view = async (name) => { await press(morph.getByRole("button", { name }).first()); await wait(900); };
  const top = () => page.evaluate(() => document.querySelectorAll("*").forEach((el) => { if (el.scrollTop) { el.scrollTop = 0; } }));

  // 1 ─ connections: what MORPH discovered in the repository
  await view("Connections"); await top();
  beat("connections"); await wait(4200);

  // 2 ─ a goal becomes a workspace
  await view("Workspace"); await top();
  const input = page.locator("#mo-goal-input");
  await press(input);
  beat("goal-typing");
  await input.fill("");
  await input.type("Triage the open issues", { delay: 55 });
  await wait(500);
  await press(page.getByRole("button", { name: /Transform workspace|Compose workspace/ }));
  beat("compose-click");
  await page.waitForFunction(() => /Triage the open issues/i.test(
    (document.querySelector(".morph .mo-topbar") || document.body).textContent), null, { timeout: 120000 });
  await page.waitForSelector(".morph .mo-metric-value", { timeout: 60000 });
  beat("workspace-ready"); await wait(3200);
  await page.mouse.move(1100, 620, { steps: 20 });
  await page.mouse.wheel(0, 520); await wait(2600);
  beat("workspace-scrolled");
  await page.mouse.wheel(0, 520); await wait(2600);

  // 3 ─ what the agent proposed, waiting for a person
  await view("Approvals"); await top();
  await page.waitForSelector(".mo-approval", { timeout: 20000 });
  beat("approvals"); await wait(5200);

  if (!REHEARSE) {
    // 4 ─ a person approves; MORPH writes to GitHub and reads the issue back
    for (let i = 0; i < 4; i++) {
      const card = page.locator(".mo-approval").first();
      if (!(await card.count())) { break; }
      const before = await page.locator(".mo-approval").count();
      await press(card.getByRole("button", { name: "Approve & apply" }));
      beat("approve-" + (i + 1));
      await page.waitForFunction((n) => document.querySelectorAll(".mo-approval").length < n, before, { timeout: 60000 });
      beat("applied-" + (i + 1));
      await wait(i === 0 ? 3000 : 1500);
    }
    await wait(2500);
    // 5 ─ the audit trail
    await view("Activity"); await top();
    beat("activity"); await wait(5000);
    // 6 ─ a different objective, a different interface
    await view("Workspace"); await top();
    await press(input);
    await input.fill("");
    beat("goal2-typing");
    await input.type("Compare issues by state", { delay: 55 });
    await wait(400);
    await press(page.getByRole("button", { name: /Transform workspace/ }));
    beat("compose2-click");
    await page.waitForFunction(() => /Compare issues by state/i.test(
      (document.querySelector(".morph .mo-topbar") || document.body).textContent), null, { timeout: 120000 });
    beat("workspace2-ready"); await wait(5000);
  }
  beat("end");
  const video = page.video();
  await context.close(); await browser.close();
  const name = REHEARSE ? "rehearsal" : "dashboard";
  fs.renameSync(await video.path(), path.join(OUT, name + ".webm"));
  fs.writeFileSync(path.join(OUT, name + ".json"), JSON.stringify(beats, null, 2));
})().catch((e) => { console.error(e); process.exit(1); });
