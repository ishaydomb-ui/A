import { expect, type Locator, type Page } from '@playwright/test';

export const PASSWORD = 'e2e-test-password-value-1';

/** Saved session files, one per role, produced by the setup project. */
export const STATE_FILES = {
  physician: '.auth/physician.json',
  editor: '.auth/editor.json',
  reviewer: '.auth/reviewer.json',
  admin: '.auth/admin.json',
} as const;

export const ACCOUNTS = {
  physician: 'physician@example.org',
  editor: 'editor@example.org',
  reviewer: 'reviewer@example.org',
  admin: 'admin@example.org',
  /** Reserved for the enrolment journey; never signed in by the setup project. */
  mfaAdmin: 'mfa-admin@example.org',
} as const;

/**
 * A unique address per call, so tests that create accounts can run in every
 * project against the same database without colliding.
 */
export function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}@example.org`;
}

export async function setLocale(page: Page, locale: 'en' | 'he'): Promise<void> {
  await page.addInitScript((value) => {
    window.localStorage.setItem('medcat.locale', value);
  }, locale);
}

/**
 * Signs in and, by default, waits until the authenticated shell is actually
 * rendered. Without that wait a following navigation can race the login
 * request — and the sign-in page has its own level-1 heading, so simply
 * waiting for a heading proves nothing.
 *
 * Pass `waitForShell: false` when the sign-in is expected to stop at a
 * second factor or to fail.
 */
export async function signIn(
  page: Page,
  who: keyof typeof ACCOUNTS = 'physician',
  password = PASSWORD,
  { waitForShell = true }: { waitForShell?: boolean } = {},
): Promise<void> {
  await page.goto('/sign-in');
  await page.getByLabel(/email|אימייל/i).fill(ACCOUNTS[who]);
  await page.getByLabel(/^password$|^סיסמה$/i).fill(password);
  await page.getByRole('button', { name: /^sign in$|^כניסה$/i }).click();

  if (waitForShell) await expectSignedIn(page);
}

/**
 * The user-menu avatar exists only once a session is established. Sign out
 * itself lives inside that menu now, so it isn't visible without opening it
 * first — the avatar is the stable, always-visible signal instead.
 */
export async function expectSignedIn(page: Page): Promise<void> {
  await expect(page.locator('.user-menu summary')).toBeVisible();
}

/** Signs in as an administrator, completing the mandatory MFA enrolment. */
export async function signInAsAdmin(
  page: Page,
  who: 'admin' | 'mfaAdmin' = 'admin',
): Promise<void> {
  await signIn(page, who, PASSWORD, { waitForShell: false });
  await expect(page.getByRole('heading', { name: /two-factor|דו-שלבי/i })).toBeVisible();

  // Read the secret out of the authenticator link rather than the on-screen
  // key: it is the value a phone would actually hand to its authenticator app,
  // so this exercises the same path a real enrolment takes.
  const otpauth = await page
    .getByRole('link', { name: /authenticator|אפליקציית האימות/i })
    .getAttribute('href');
  const secret = new URL(otpauth!).searchParams.get('secret');
  if (!secret) throw new Error(`no secret in otpauth URL: ${otpauth}`);

  await page.getByLabel(/authentication code|קוד אימות/i).fill(await totp(secret));
  await page.getByRole('button', { name: /verify|אימות/i }).click();

  // On first enrolment the recovery codes are shown once and the session is
  // withheld until they are acknowledged. On later sign-ins this step is
  // absent, so wait for whichever screen actually appears.
  const acknowledge = page.getByRole('button', { name: /saved these codes|שמרתי/i });
  const signOut = page.getByRole('button', { name: /sign out|התנתקות/i });
  await expect(acknowledge.or(signOut)).toBeVisible();
  if (await acknowledge.count()) await acknowledge.click();

  await expectSignedIn(page);
}

/** Generates a TOTP code, so the MFA flow can be driven for real. */
export async function totp(secret: string): Promise<string> {
  const { authenticator } = await import('otplib');
  return authenticator.generate(secret);
}

export async function search(page: Page, query: string): Promise<void> {
  const box = page.getByRole('combobox');
  await box.fill(query);
  await box.press('Enter');
}

/**
 * Makes the filter groups visible and interactable.
 *
 * On a wide viewport they sit in a persistent sidebar and are already
 * visible — there is nothing to open. On a narrow viewport they live behind
 * a "Filters" button that opens a modal sheet, so open that instead.
 */
export async function openFilters(page: Page): Promise<void> {
  const trigger = page.getByRole('button', { name: /^filters|^מסננים/i });
  if (await trigger.isVisible().catch(() => false)) {
    await trigger.click();
    await expect(page.locator('dialog.sheet')).toBeVisible();
  } else {
    await expect(page.locator('.filter-panel-desktop')).toBeVisible();
  }
}

/**
 * Opens the user menu (administration, language, theme, account, sign out)
 * and returns a locator scoped to its panel. A closed <details> hides its
 * content from the accessibility tree, so the panel has to be opened before
 * anything inside it is reachable — at any viewport, since it is the only
 * way to reach these controls regardless of width.
 */
export async function openUserActions(page: Page): Promise<Locator> {
  const menu = page.locator('.user-menu');
  await menu.locator('summary').click();
  await expect(menu.locator('.user-menu-panel')).toBeVisible();
  return menu;
}

/**
 * Opens the comparison for whatever is currently selected.
 *
 * On a wide viewport that is the floating compare tray's own "Compare
 * selected" button; on a phone the tray is hidden in favour of the bottom
 * nav's Compare tab, which carries the same selection in its own link.
 */
export async function openCompare(page: Page): Promise<void> {
  const trayButton = page.getByRole('button', { name: /compare selected/i });
  if (await trayButton.isVisible().catch(() => false)) {
    await trayButton.click();
  } else {
    await page.locator('.bottom-nav-item', { hasText: /compare|השוואה/i }).click();
  }
}
