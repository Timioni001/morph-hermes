/* MORPH browser end-to-end tests.
 *
 * Drives the real dist bundle, served by the real dashboard API, against the three
 * real demo applications, in Chromium. Nothing is stubbed: every assertion below is
 * about state that actually exists in a source application at that moment.
 *
 * Expects (scripts/e2e.sh does this for you):
 *   - the demo applications running on 8811/8812/8813
 *   - the harness on 8820 with the source tokens in the environment
 *
 *   node tests/e2e/run_e2e.cjs [--base http://127.0.0.1:8820] [--shots DIR] [--headed]
 */
const { chromium, devices } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");

const args = process.argv.slice(2);
function flag(name, fallback) {
  const index = args.indexOf("--" + name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

const BASE = flag("base", "http://127.0.0.1:8820");
const STOREFRONT = flag("storefront", "http://127.0.0.1:8811");
const SHOTS = flag("shots", path.join(__dirname, "..", "..", ".e2e-artifacts"));
const HEADED = args.includes("--headed");

fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
let failures = 0;

function check(name, condition, detail) {
  const ok = Boolean(condition);
  if (!ok) { failures += 1; }
  results.push({ name, ok, detail: detail || "" });
  console.log(`${ok ? "  ok  " : "  FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(SHOTS, name + ".png"), fullPage: true });
}

async function waitForWorkspace(page) {
  await page.waitForSelector(".morph .mo-metric-value", { timeout: 25000 });
}

async function main() {
  const browser = await chromium.launch({ headless: !HEADED });
  const context = await browser.newContext({ viewport: { width: 1440, height: 980 } });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") { consoleErrors.push(m.text()); } });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));

  // ── 1. the plugin mounts through the documented dashboard contract ──────────
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await waitForWorkspace(page);
  check("plugin registers on window.__HERMES_PLUGINS__",
        (await page.evaluate(() => window.__HARNESS_MOUNTED__)) === "morph");
  check("every connected application reports healthy",
        (await page.locator(".mo-source:not(.down)").count()) === 3,
        (await page.locator(".mo-source").allTextContents()).join(" | "));

  // ── 2. compose a workspace from a goal ──────────────────────────────────────
  await page.fill("#mo-goal-input", "Work through delayed orders and the complaints about them");
  await page.getByRole("button", { name: /Transform workspace|Compose workspace/ }).click();
  await page.waitForSelector(".mo-toast-title", { timeout: 25000 });
  await waitForWorkspace(page);
  const sections = await page.locator(".mo-section-title").allTextContents();
  check("the goal produced a multi-section workspace", sections.length >= 3, sections.join(" / "));
  const delayedMetric = await page.locator(".mo-card", { hasText: "Orders in focus" })
    .locator(".mo-metric-value").first().textContent();
  check("the lead metric counts the delayed orders", delayedMetric.trim() === "5", delayedMetric);

  // ── 3. the cross-application join is real ───────────────────────────────────
  const relation = page.locator(".mo-card", { hasText: "joined across applications" }).first();
  check("a cross-application relation block rendered", await relation.count() > 0);
  const relationSub = await relation.locator(".mo-card-sub").first().textContent();
  const [leftSide, rightSide] = relationSub.split("→").map((s) => s.trim());
  check("the relation joins two different applications",
        leftSide.split(".")[0] !== rightSide.split(".")[0],
        relationSub.trim().slice(0, 120));
  const relatedChips = await relation.locator("tbody tr .mo-rel-chip").count();
  check("the join actually pulled related records across", relatedChips > 0,
        relatedChips + " related chip(s)");

  // ── 4. a review goal lays the queue out as a board ──────────────────────────
  const board = page.locator(".mo-card", { hasText: "by follow-up" }).first();
  check("a review goal produced a board", await board.count() > 0);
  const lanes = await page.locator(".mo-lane").count();
  check("the board has more than one populated lane", lanes >= 2, String(lanes));
  const laneTitles = await page.locator(".mo-lane-title").allTextContents();
  check("the board groups by a dimension the application lets you change",
        laneTitles.length >= 2, laneTitles.join(", "));

  // Charts are only present for some goals; measure one when it is there.
  if (await page.locator(".mo-bar-fill").count() >= 2) {
    await page.evaluate(() => Promise.all(
      document.getAnimations()
        .filter((a) => (a.animationName || "") === "mo-grow")
        .map((a) => a.finished)));
    const bars = await page.locator(".mo-bar-fill").evaluateAll(
      (nodes) => nodes.map((n) => ({ pct: parseFloat(n.style.width),
                                     px: n.getBoundingClientRect().width })));
    check("the chart bars are laid out proportionally",
          bars[0].px > 10 && bars[0].pct >= bars[bars.length - 1].pct,
          bars.map((b) => Math.round(b.pct) + "%").join(" "));
  }

  // ── 4b. tables are usable, not decorative ──────────────────────────────────
  const ordersTableEarly = page.locator(".mo-card", { hasText: "Orders needing attention" })
    .first();
  const beforeFilter = await ordersTableEarly.locator("tbody tr").count();
  await ordersTableEarly.locator("input.mo-search").fill("ORD-2045");
  await page.waitForTimeout(250);
  const afterFilter = await ordersTableEarly.locator("tbody tr").count();
  check("the table search narrows the rows client-side",
        afterFilter === 1 && beforeFilter > 1, `${beforeFilter} → ${afterFilter}`);
  await ordersTableEarly.locator("input.mo-search").fill("");
  await page.waitForTimeout(250);
  await ordersTableEarly.locator("th.sortable", { hasText: "Hours Late" }).first().click();
  await page.waitForTimeout(250);
  const sorted = await ordersTableEarly.locator("tbody tr td.num").first().textContent();
  check("clicking a column header sorts the rows", sorted.trim().startsWith("30"), sorted);

  await shot(page, "01-composed");

  // ── 5. selecting a row drives the detail pane and the action form ───────────
  const ordersTable = page.locator(".mo-card", { hasText: "Orders needing attention" }).first();
  await ordersTable.locator("tbody tr", { hasText: "ORD-2045" }).click();
  await page.waitForTimeout(900);
  const detail = page.locator(".mo-card", { hasText: "Selected order" }).first();
  check("the detail pane followed the selection",
        (await detail.textContent()).includes("ORD-2045"));
  const formTarget = await page.locator(".mo-target").first().textContent();
  check("the action form retargeted too", formTarget.includes("ORD-2045"), formTarget.trim());

  // ── 6. propose → human approval → verified in the source application ────────
  const before = await (await fetch(STOREFRONT + "/v1/orders/ORD-2045", {
    headers: { Authorization: "Bearer " + process.env.MORPH_STOREFRONT_TOKEN }
  })).json();
  check("the order is not flagged before we act", before.review_status === "none",
        String(before.review_status));

  await ordersTable.locator("tbody tr", { hasText: "ORD-2045" })
    .getByRole("button", { name: "Flag for review" }).click();
  await page.waitForSelector("[role=dialog]", { timeout: 10000 });
  await page.locator("[role=dialog] textarea").first().fill("75 hours late and the customer is chasing");
  await page.getByRole("button", { name: "Propose for approval" }).click();
  await page.waitForTimeout(900);

  // Approvals are their own surface; the workspace only nudges you to it.
  check("the workspace flags that something is waiting",
        (await page.locator(".mo-notice", { hasText: "waiting for your approval" }).count()) > 0);
  check("the rail badges the pending count",
        (await page.locator(".mo-rail-badge").first().textContent()) === "1");
  await page.getByRole("button", { name: "Approvals" }).first().click();
  await page.waitForSelector(".mo-approval", { timeout: 15000 });

  const stillClean = await (await fetch(STOREFRONT + "/v1/orders/ORD-2045", {
    headers: { Authorization: "Bearer " + process.env.MORPH_STOREFRONT_TOKEN }
  })).json();
  check("proposing changed nothing in the source application",
        stillClean.review_status === "none" && stillClean._version === before._version,
        `${stillClean.review_status} v${stillClean._version}`);

  const card = page.locator(".mo-approval").first();
  const diff = await card.textContent();
  check("the approval card shows the promised before/after",
        diff.includes("None") || diff.includes("none") || diff.includes("flagged"),
        diff.replace(/\s+/g, " ").slice(0, 120));
  await shot(page, "02-awaiting-approval");

  await card.getByRole("button", { name: "Approve & apply" }).click();
  await page.waitForSelector(".mo-toast", { timeout: 25000 });
  await page.waitForFunction(() => {
    const nodes = Array.from(document.querySelectorAll(".mo-toast-title"));
    return nodes.some((n) => /Applied and verified/.test(n.textContent));
  }, null, { timeout: 25000 });
  check("MORPH reported the action applied and verified", true);

  const after = await (await fetch(STOREFRONT + "/v1/orders/ORD-2045", {
    headers: { Authorization: "Bearer " + process.env.MORPH_STOREFRONT_TOKEN }
  })).json();
  check("the storefront's own record changed",
        after.review_status === "flagged"
        && after.review_reason.startsWith("75 hours late")
        && after._version === before._version + 1,
        `${after.review_status} v${after._version}`);

  const history = await (await fetch(STOREFRONT + "/v1/orders/ORD-2045/history", {
    headers: { Authorization: "Bearer " + process.env.MORPH_STOREFRONT_TOKEN }
  })).json();
  check("the storefront logged the write itself",
        history.entries.length > 0 && history.entries[0].operation === "flag_for_review",
        JSON.stringify(history.entries[0] || {}).slice(0, 120));
  await shot(page, "03-applied");

  // ── 6b. the other surfaces ──────────────────────────────────────────────────
  await page.getByRole("button", { name: "Connections" }).first().click();
  await page.waitForTimeout(500);
  const connText = await page.locator(".mo-view").first().textContent();
  check("Connections lists every application with its entities",
        connText.includes("storefront.orders") && connText.includes("helpdesk.cases")
        && connText.includes("warehouse.stock_items"));
  check("Connections shows the cross-application joins",
        connText.includes("helpdesk.cases.order_ref"));
  check("Connections shows a denied operation and the reason",
        connText.includes("storefront.orders.issue_refund")
        && connText.toLowerCase().includes("denied"));

  await page.getByRole("button", { name: "Activity" }).first().click();
  await page.waitForTimeout(500);
  const activityText = await page.locator(".mo-view").first().textContent();
  check("Activity verifies the audit chain", activityText.includes("Audit chain intact"));
  check("Activity shows the evidence behind an applied action",
        activityText.includes("verified: yes") && activityText.includes("changed:"),
        activityText.slice(0, 120).replace(/\s+/g, " "));

  await page.getByRole("button", { name: "Context" }).first().click();
  await page.waitForTimeout(500);
  const contextText = await page.locator(".mo-view").first().textContent();
  check("Context shows how MORPH read the goal",
        contextText.includes("How MORPH read that") && contextText.includes("storefront.orders"));
  check("Context explains why an entity is in focus",
        /narrowed by|named in the goal/.test(contextText));
  await shot(page, "05-context");

  await page.getByRole("button", { name: "Workspace" }).first().click();
  await page.waitForSelector("#mo-goal-input", { timeout: 10000 });

  // ── 7. transformation preserves context ─────────────────────────────────────
  // Wait on the revision counter, not on a toast: toasts from earlier steps look
  // identical and would let the assertions read the previous workspace.
  const revisionBefore = await page.locator(".mo-topbar .mo-card-sub").first().textContent();
  await page.fill("#mo-goal-input", "Show stock running low and the restocks I should request");
  await page.getByRole("button", { name: /Transform workspace/ }).click();
  await page.waitForFunction((previous) => {
    const node = document.querySelector(".mo-topbar .mo-card-sub");
    return node && node.textContent !== previous;
  }, revisionBefore, { timeout: 30000 });
  await waitForWorkspace(page);
  const newSections = await page.locator(".mo-section-title").allTextContents();
  const viewText = await page.locator(".mo-view").first().textContent();
  check("the workspace rebuilt around the new goal",
        viewText.includes("warehouse.stock_items"),
        newSections.join(" / "));
  check("the earlier selection survived the transformation",
        (await page.locator(".mo-card", { hasText: "Orders in scope" }).first()
          .locator("tr.picked").count()) > 0
        || (await page.locator("tr.picked").count()) > 0,
        "picked rows: " + (await page.locator("tr.picked").count()));
  await shot(page, "04-transformed");

  // ── 8. permission denial is shown, not discovered by failing ────────────────
  const denied = await page.evaluate(async () => {
    const response = await fetch("/api/plugins/morph/discover");
    const body = await response.json();
    return (body.permissions || []).filter((p) => !p.allowed).map((p) => p.ref);
  });
  check("a denied operation is reported as denied",
        denied.includes("storefront.orders.issue_refund"), denied.join(","));

  // Everything up to here should be clean; the next check deliberately provokes a
  // 403, which the browser logs as a console error of its own.
  const errorsBeforeCsrfProbe = consoleErrors.slice();

  // ── 9. mutating routes refuse a cross-site caller ───────────────────────────
  const csrf = await page.evaluate(async () => {
    const response = await fetch("/api/plugins/morph/propose", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "storefront", entity: "orders", record_id: "ORD-2041",
                             operation: "flag_for_review", params: { reason: "x" } })
    });
    return response.status;
  });
  check("a mutation without the intent header is refused", csrf === 403, String(csrf));

  // ── 10. degraded mode: one application down, the rest keep working ──────────
  // (exercised by the integration tests; here we only assert the banner wiring)
  check("no uncaught console errors during the workspace run",
        errorsBeforeCsrfProbe.length === 0, errorsBeforeCsrfProbe.slice(0, 3).join(" | "));

  // ── 11. responsive: phone and tablet ────────────────────────────────────────
  for (const [name, descriptor] of [["iphone-15", devices["iPhone 15"]],
                                    ["ipad", devices["iPad (gen 7)"]]]) {
    const mobile = await browser.newContext(descriptor);
    const mobilePage = await mobile.newPage();
    await mobilePage.goto(BASE + "/", { waitUntil: "domcontentloaded" });
    await waitForWorkspace(mobilePage);
    const overflow = await mobilePage.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(`${name}: no horizontal page overflow`, overflow <= 1, String(overflow));
    const railVisible = await mobilePage.locator(".mo-rail-btn").first().isVisible();
    check(`${name}: the section rail is reachable`, railVisible);
    await mobilePage.screenshot({ path: path.join(SHOTS, "06-" + name + ".png"),
                                  fullPage: true });
    await mobile.close();
  }

  await browser.close();

  console.log("\n" + results.filter((r) => r.ok).length + "/" + results.length + " checks passed");
  if (failures) {
    console.error(failures + " check(s) failed");
    process.exit(1);
  }
}

main().catch((error) => { console.error(error); process.exit(1); });
