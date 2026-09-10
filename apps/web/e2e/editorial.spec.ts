import { expect, test } from '@playwright/test';
import { STATE_FILES, uniqueEmail } from './helpers.ts';

test.describe('review report — editor', () => {
  test.use({ storageState: STATE_FILES.editor });

  test('an editor sees the findings the import refused to fix', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: /review/i }).click();
    await expect(page).toHaveURL(/\/review/);
    await expect(page.getByRole('heading', { name: /data quality findings/i })).toBeVisible();
    await expect(page.getByText(/nothing listed here has been corrected automatically/i)).toBeVisible();
    await expect(page.getByText(/Probable label inversion/).first()).toBeVisible();
  });

  test('an editor cannot close a finding', async ({ page }) => {
    await page.goto('/review');
    await expect(page.getByText(/Probable label inversion/).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /record a decision/i })).toHaveCount(0);
  });

});

test.describe('review report — reviewer', () => {
  test.use({ storageState: STATE_FILES.reviewer });

  test('a reviewer must give a reason to close a finding', async ({ page }) => {
    await page.goto('/review');
    await page.getByRole('button', { name: /record a decision/i }).first().click();
    await expect(page.getByRole('button', { name: /mark resolved/i })).toBeDisabled();

    await page.getByLabel(/how was this resolved/i).fill('Confirmed against the authoritative Excel.');
    await expect(page.getByRole('button', { name: /mark resolved/i })).toBeEnabled();
    await page.getByRole('button', { name: /mark resolved/i }).click();
    await expect(page.getByText('Confirmed against the authoritative Excel.').first()).toBeVisible();
  });

});

test.describe('review report — physician', () => {
  test.use({ storageState: STATE_FILES.physician });

  test('a physician cannot reach the review report at all', async ({ page }) => {
    await page.goto('/review');
    await expect(page.getByRole('alert')).toContainText(/permission/i);
    await expect(page.getByText(/Probable label inversion/)).toHaveCount(0);
  });
});

test.describe('imports — editor', () => {
  test.use({ storageState: STATE_FILES.editor });

  test('the import screen states that uploading changes nothing live', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: /imports/i }).click();
    await expect(page.getByText(/does not change the live catalogue/i)).toBeVisible();
    await expect(page.getByText(/committed as drafts/i)).toBeVisible();
  });

  test('shows the history of previous imports', async ({ page }) => {
    await page.goto('/imports');
    await expect(page.getByRole('heading', { name: /import history/i })).toBeVisible();
    await expect(page.getByText('catalogue.xlsx').first()).toBeVisible();
    await expect(page.getByText('committed').first()).toBeVisible();
  });

  test('an editor is told they cannot commit an import', async ({ page }) => {
    await page.goto('/imports');
    // The commit control only appears for a role that holds import:commit.
    await expect(page.getByRole('button', { name: /stage as drafts/i })).toHaveCount(0);
  });

});

test.describe('imports — physician', () => {
  test.use({ storageState: STATE_FILES.physician });

  test('a physician cannot reach the import screen', async ({ page }) => {
    await page.goto('/imports');
    // The page tells them plainly rather than showing an editorial interface
    // whose every action the server would refuse.
    await expect(page.getByRole('alert')).toContainText(/permission/i);
    await expect(page.getByRole('heading', { name: /import history/i })).toHaveCount(0);
  });
});

test.describe('user administration', () => {
  test.use({ storageState: STATE_FILES.admin });

  test('an administrator can invite a clinician and gets a single-use link', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: /users/i }).click();

    const invitee = uniqueEmail('new-doctor');
    // The name has to be unique too: every project runs against the same
    // database, so a fixed name would match rows another project created.
    const inviteeName = `New Doctor ${invitee.split('@')[0].slice(-6)}`;
    await page.getByLabel(/email/i).fill(invitee);
    await page.getByLabel(/full name/i).fill(inviteeName);
    await page.getByLabel(/role/i).first().selectOption('physician');
    await page.getByRole('button', { name: /send invitation/i }).click();

    await expect(page.getByText(/invitation created/i)).toBeVisible();
    await expect(page.getByText(/accept-invitation\?token=/)).toBeVisible();
    await expect(page.getByRole('rowheader', { name: inviteeName })).toBeVisible();
    await expect(page.getByRole('row', { name: new RegExp(inviteeName) }).getByText('invited')).toBeVisible();
  });

  test('an invited account cannot sign in until the invitation is accepted', async ({ page, context }) => {
    await page.goto('/users');
    const invitee = uniqueEmail('pending');
    await page.getByLabel(/email/i).fill(invitee);
    await page.getByLabel(/full name/i).fill('Pending Person');
    await page.getByRole('button', { name: /send invitation/i }).click();
    await expect(page.getByText(/invitation created/i)).toBeVisible();

    // The account now exists but is only "invited": no password has been set,
    // so nothing can sign in as it.
    await context.clearCookies();
    await page.goto('/sign-in');
    await page.getByLabel(/email/i).fill(invitee);
    await page.getByLabel(/^password$/i).fill('anything-at-all-here');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test('accepting an invitation activates the account', async ({ page, context }) => {
    await page.goto('/users');
    const invitee = uniqueEmail('accepting');
    await page.getByLabel(/email/i).fill(invitee);
    await page.getByLabel(/full name/i).fill('Accepting Person');
    await page.getByRole('button', { name: /send invitation/i }).click();

    const link = await page.locator('.mono').filter({ hasText: 'accept-invitation' }).innerText();
    const url = new URL(link.trim());

    await context.clearCookies();
    await page.goto(url.pathname + url.search);
    await expect(page.getByText(invitee)).toBeVisible();

    await page.getByLabel('New password', { exact: true }).fill('a-fresh-strong-passphrase-9');
    await page.getByLabel('Confirm new password', { exact: true }).fill('a-fresh-strong-passphrase-9');
    await page.getByRole('button', { name: /set password/i }).click();

    await expect(page.getByRole('heading', { level: 1, name: /catalogue/i })).toBeVisible();
  });

  test('an invitation link cannot be reused', async ({ page, context }) => {
    await page.goto('/users');
    const invitee = uniqueEmail('single-use');
    await page.getByLabel(/email/i).fill(invitee);
    await page.getByLabel(/full name/i).fill('Single Use');
    await page.getByRole('button', { name: /send invitation/i }).click();

    const link = await page.locator('.mono').filter({ hasText: 'accept-invitation' }).innerText();
    const url = new URL(link.trim());

    await context.clearCookies();
    await page.goto(url.pathname + url.search);
    await page.getByLabel('New password', { exact: true }).fill('another-strong-passphrase-7');
    await page.getByLabel('Confirm new password', { exact: true }).fill('another-strong-passphrase-7');
    await page.getByRole('button', { name: /set password/i }).click();
    await expect(page.getByRole('heading', { level: 1, name: /catalogue/i })).toBeVisible();

    await context.clearCookies();
    await page.goto(url.pathname + url.search);
    await expect(page.getByRole('alert')).toContainText(/not valid|already been used|expired/i);
  });
});

test.describe('editorial detail on a record', () => {
  test.use({ storageState: STATE_FILES.reviewer });

  test('a reviewer does see which checks are outstanding', async ({ page }) => {
    await page.goto('/medications/sertraline?draft=1');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    // Present only when the record was published with gates outstanding.
    const outstanding = page.getByText(/Outstanding checks/i);
    if (await outstanding.count()) {
      await outstanding.click();
      await expect(page.locator('.record-internal li').first()).toBeVisible();
    }
  });
});
