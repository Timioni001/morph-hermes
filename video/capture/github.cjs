/* Still captures of the scratch repository on github.com itself, logged out.
 *   node video/capture/github.cjs --out <dir> --tag before|after
 */
const { chromium } = require("playwright");
const path = require("node:path");
const fs = require("node:fs");
const args = process.argv.slice(2);
const flag = (n, d) => { const i = args.indexOf("--" + n); return i >= 0 ? args[i + 1] : d; };
const OUT = path.resolve(flag("out", "video/footage"));
const TAG = flag("tag", "before");
const REPO = "https://github.com/Timioni001/morph-demo";
fs.mkdirSync(OUT, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2,
                                       colorScheme: "light" });
  const shot = async (url, name) => {
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 12000 }).catch(() => {});
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, `github-${TAG}-${name}.png`) });
    console.log("captured", name);
  };
  await shot(REPO + "/issues", "open");
  await shot(REPO + "/issues?q=is%3Aissue%20state%3Aclosed", "closed");
  await shot(REPO + "/issues/3", "issue3");
  await shot(REPO + "/issues/7", "issue7");
  await shot(REPO + "/issues/1", "issue1");
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
