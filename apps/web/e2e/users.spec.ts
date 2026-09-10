import { expect, test } from '@playwright/test';
import { STATE_FILES, uniqueEmail } from './helpers.ts';

test.describe('account administration', () => {
  test.use({ storageState: STATE_FILES.admin });

  /** Invites an account and returns the row it created in the accounts table. */
  async function invite(page: import('@playwright/test').Page, name: string) {
    const email = uniqueEmail('rename');
    await page.goto('/users');
    await page.getByLabel(/email address/i).fill(email);
    await page.getByLabel(/full name/i).fill(name);
    await page.getByRole('button', { name: /send invitation/i }).click();
    await expect(page.getByText(/invitation created/i)).toBeVisible();
    return { email, row: page.getByRole('row').filter({ hasText: email }) };
  }

  test('an administrator corrects a name that was recorded wrongly', async ({ page }) => {
    const { row } = await invite(page, 'Liri');
    await expect(row.getByRole('rowheader')).toContainText('Liri');

    await row.getByRole('button', { name: /rename liri/i }).click();
    const field = row.getByRole('textbox');
    await expect(field).toBeFocused();
    await field.fill('Liran Korotkin Barzelay');
    await row.getByRole('button', { name: /^save$/i }).click();

    await expect(row.getByRole('rowheader')).toContainText('Liran Korotkin Barzelay');

    // The corrected name survives a reload, so it was stored rather than only
    // shown.
    await page.reload();
    await expect(row.getByRole('rowheader')).toContainText('Liran Korotkin Barzelay');
  });

  test('cancelling a rename leaves the recorded name untouched', async ({ page }) => {
    const { row } = await invite(page, 'Unchanged Name');

    await row.getByRole('button', { name: /rename unchanged name/i }).click();
    await row.getByRole('textbox').fill('Something Else');
    await row.getByRole('button', { name: /cancel|ביטול/i }).click();

    await expect(row.getByRole('rowheader')).toContainText('Unchanged Name');
    await page.reload();
    await expect(row.getByRole('rowheader')).toContainText('Unchanged Name');
  });

  test('an invitation can be reissued once the site has a reachable address', async ({ page }) => {
    // An invitation link is built from the address the API is configured with,
    // so one created before the site was reachable has to be replaceable.
    const { row } = await invite(page, 'Not Yet Accepted');
    const link = page.getByText(/accept-invitation\?token=/);
    const first = (await link.innerText()).trim();

    await row.getByRole('button', { name: /re-invite/i }).click();

    // The old link is cleared before the request goes out, so poll for a link
    // that is present *and* different rather than for whatever is on screen.
    await expect
      .poll(async () => {
        if ((await link.count()) !== 1) return null;
        const shown = (await link.innerText()).trim();
        return shown === first ? null : shown;
      })
      .toMatch(/accept-invitation\?token=/);
  });

  test('the rename field is reachable and labelled for a screen reader', async ({ page }) => {
    const { email, row } = await invite(page, 'Keyboard Reachable');

    await row.getByRole('button', { name: /rename keyboard reachable/i }).click();
    // The label names the account, because the visible name is replaced by the
    // field itself while the edit is open.
    await expect(row.getByLabel(`Full name for ${email}`)).toBeVisible();

    // Escape is the expected way out of an inline edit.
    await row.getByRole('textbox').press('Escape');
    await expect(row.getByRole('rowheader')).toContainText('Keyboard Reachable');
  });
});
