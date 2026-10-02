// Proves a deployed Otii Learn the way a person meets it, and saves two pictures.
//   OTII_URL=https://staging.get-otii.com LEARN_URL=https://learn-staging.get-otii.com \
//   EMAIL=... PASSWORD=... PLAYWRIGHT_FROM=/path/to/otii/frontend/package.json \
//   node otii/checks/deployed.mjs <folder for pictures>
// 1. Otii Learn's own home page: every image loads (the logo comes from the bucket).
// 2. Sign in to otii, open Otii Learn from the menu: it opens inside otii, signed in,
//    with no sign-in page of its own. Exit code 1 if either fails.
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import path from "node:path";

for (const key of ["OTII_URL", "LEARN_URL", "EMAIL", "PASSWORD", "PLAYWRIGHT_FROM"]) if (!process.env[key]) throw new Error(`set ${key}`);
const { chromium } = createRequire(process.env.PLAYWRIGHT_FROM)("@playwright/test");
const out = path.resolve(process.argv[2] ?? "otii/.state/checks");
mkdirSync(out, { recursive: true });
const learn = new URL(process.env.LEARN_URL);
let failed = false;
const browser = await chromium.launch();
try {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.goto(learn.href, { waitUntil: "networkidle", timeout: 120000 });
  const broken = await page.evaluate(() => [...document.images].filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.src));
  const fromBucket = await page.evaluate(() => [...document.images].filter((i) => i.src.includes("/content/")).length);
  await page.screenshot({ path: path.join(out, "learn-home.png") });
  console.log(`home: images from the bucket ${fromBucket}, broken ${broken.length}`);
  if (broken.length || !fromBucket) failed = true;

  await page.goto(process.env.OTII_URL, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForURL(/\/realms\//, { timeout: 120000 });
  await page.fill("#username", process.env.EMAIL);
  await page.fill("#password", process.env.PASSWORD);
  await page.click("#kc-login");
  await page.waitForURL((u) => u.href.startsWith(process.env.OTII_URL) && !u.pathname.startsWith("/callback"), { timeout: 90000 });
  await page.waitForLoadState("networkidle");
  const notNow = page.getByRole("button", { name: "Not now" });
  if (await notNow.isVisible().catch(() => false)) await notNow.click();
  await page.getByRole("link", { name: "Otii Learn" }).first().click();
  await page.waitForURL(/\/learn/, { timeout: 60000 });
  const frame = page.frames().find((f) => f !== page.mainFrame()) ?? (await page.waitForEvent("frameattached"));
  await frame.waitForURL((u) => u.host === learn.host && !u.pathname.startsWith("/auth") && !u.pathname.startsWith("/api"), { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(4000);
  const where = new URL(frame.url());
  const text = (await frame.locator("body").innerText().catch(() => "")).replace(/\s+/g, " ");
  const signedIn = where.host === learn.host && !/sign in|log in|authentication failed/i.test(text.slice(0, 400)) && text.includes("Courses");
  await page.screenshot({ path: path.join(out, "learn-inside-otii.png") });
  console.log(`inside otii: frame at ${where.host}${where.pathname}, signed in ${signedIn}`);
  if (!signedIn) failed = true;
} catch (error) {
  console.error(String(error).slice(0, 300));
  failed = true;
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
