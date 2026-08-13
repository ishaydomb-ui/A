/**
 * Screenshot the running app at phone size, for review.
 *   npx tsx scripts/screenshot.ts http://localhost:PORT
 */
import { chromium } from "playwright";
import fs from "node:fs";

const base = process.argv[2] ?? "http://localhost:3000";
const out = "/tmp/shots";
fs.mkdirSync(out, { recursive: true });

const PAGES: Array<{ path: string; name: string; theme: "light" | "dark" }> = [
  { path: "/", name: "today", theme: "light" },
  { path: "/together", name: "together", theme: "light" },
  { path: "/facts", name: "facts", theme: "light" },
  { path: "/approvals", name: "approvals", theme: "light" },
  { path: "/food", name: "food", theme: "dark" },
  { path: "/skills", name: "skills", theme: "dark" },
];

async function main() {
  // The preinstalled Chromium may not match this Playwright's expected build,
  // so point at it explicitly rather than trying to download one.
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || undefined,
  });

  for (const page of PAGES) {
    const context = await browser.newContext({
      viewport: { width: 402, height: 900 },
      deviceScaleFactor: 2,
      locale: "he-IL",
      timezoneId: "Asia/Jerusalem",
      colorScheme: page.theme,
    });
    const tab = await context.newPage();
    await tab.goto(`${base}${page.path}`, { waitUntil: "networkidle", timeout: 30_000 });
    // A fixed bottom bar is captured at its viewport position, so in a
    // full-page shot it lands in the middle of the page and looks like a bug.
    // Let it flow to the end of the document instead - this is a capture
    // concern only, the app is untouched.
    await tab.addStyleTag({
      content: "nav.fixed{position:static !important} body{padding-bottom:0 !important}",
    });
    // Let fonts settle so text doesn't shift mid-capture.
    await tab.waitForTimeout(600);
    await tab.screenshot({ path: `${out}/${page.name}.png`, fullPage: true });
    console.log(`  ${page.name} (${page.theme})`);
    await context.close();
  }

  await browser.close();
  console.log(`\nSaved to ${out}`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
