import { expect, test } from '@playwright/test';
import { ACCOUNTS, PASSWORD, signIn, signInAsAdmin } from './helpers.ts';

test.describe('access control', () => {
  test('the catalogue is behind a login wall', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/sign-in/);
    await expect(page.getByRole('heading', { name: /sign in/i })).toBeVisible();
  });

  test('a medication page is not reachable without signing in', async ({ page }) => {
    await page.goto('/medications/sertraline');
    await expect(page).toHaveURL(/\/sign-in/);
    await expect(page.getByText(/Sertraline/)).toHaveCount(0);
  });

  test('there is no public registration', async ({ page }) => {
    await page.goto('/sign-in');
    await expect(page.getByText(/created by an administrator/i)).toBeVisible();
    await expect(page.getByRole('link', { name: /register|sign up|create account/i })).toHaveCount(0);
  });

  test('a wrong password is rejected', async ({ page }) => {
    await signIn(page, 'physician', 'not-the-right-password', { waitForShell: false });
    await expect(page.getByRole('alert')).toContainText(/incorrect/i);
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test('signs in and returns to the requested page', async ({ page }) => {
    await page.goto('/medications/sertraline');
    await page.getByLabel(/email/i).fill(ACCOUNTS.physician);
    await page.getByLabel(/^password$/i).fill(PASSWORD);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/medications\/sertraline/);
    await expect(page.getByRole('heading', { level: 1, name: 'Sertraline' })).toBeVisible();
  });

  test('signing out ends the session', async ({ page }) => {
    await signIn(page, 'physician');
    await page.getByRole('button', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/sign-in/);

    await page.goto('/');
    await expect(page).toHaveURL(/\/sign-in/);
  });
});

test.describe('multi-factor authentication', () => {
  test('an administrator must enrol before getting a session', async ({ page }) => {
    await signIn(page, 'mfaAdmin', PASSWORD, { waitForShell: false });
    await expect(page.getByRole('heading', { name: /set up two-factor/i })).toBeVisible();
    // No session yet: the catalogue is still out of reach.
    await page.goto('/');
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test('offers a one-tap link to an authenticator app', async ({ page }) => {
    // A QR code cannot be scanned from the screen displaying it, so on a phone
    // this link is the only workable route into enrolment.
    await signIn(page, 'mfaAdmin', PASSWORD, { waitForShell: false });
    const link = page.getByRole('link', { name: /authenticator/i });
    await expect(link).toBeVisible();

    const href = await link.getAttribute('href');
    expect(href).toMatch(/^otpauth:\/\/totp\//);
    expect(new URL(href!).searchParams.get('secret')).toMatch(/^[A-Z2-7]+$/);

    // The key and the QR stay available for setting it up elsewhere.
    await page.getByText(/another device/i).click();
    await expect(page.getByRole('img', { name: /QR code/i })).toBeVisible();
  });

  test('completing enrolment signs the administrator in', async ({ page }) => {
    await signInAsAdmin(page, 'mfaAdmin');
    await expect(page.getByRole('heading', { level: 1, name: /catalogue/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /users/i })).toBeVisible();
  });
});

test.describe('role visibility', () => {
  test('a physician sees no editorial navigation', async ({ page }) => {
    await signIn(page, 'physician');
    const nav = page.getByRole('navigation', { name: /main navigation/i });
    await expect(nav.getByRole('link', { name: /users/i })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: /imports/i })).toHaveCount(0);
    await expect(nav.getByRole('link', { name: /review/i })).toHaveCount(0);
  });

  test('an editor sees review and imports but not users', async ({ page }) => {
    await signIn(page, 'editor');
    const nav = page.getByRole('navigation', { name: /main navigation/i });
    await expect(nav.getByRole('link', { name: /review/i })).toBeVisible();
    await expect(nav.getByRole('link', { name: /imports/i })).toBeVisible();
    await expect(nav.getByRole('link', { name: /users/i })).toHaveCount(0);
  });

  test('the server refuses a physician who navigates to an admin screen directly', async ({ page }) => {
    await signIn(page, 'physician');
    await page.goto('/users');
    // The page loads but the API refuses, so no user data is ever rendered.
    await expect(page.getByRole('alert')).toContainText(/does not permit|permission|forbidden/i);
    await expect(page.getByRole('table')).toHaveCount(0);
  });
});
