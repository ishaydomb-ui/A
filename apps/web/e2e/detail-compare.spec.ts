import { expect, test } from '@playwright/test';
import { STATE_FILES, search } from './helpers.ts';

test.use({ storageState: STATE_FILES.physician });

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.result-row').first()).toBeVisible();
});

test.describe('medication detail', () => {
  test('opens the full record from a result row', async ({ page }) => {
    await search(page, 'methylphenidate');
    await page.getByRole('link', { name: /Methylphenidate/ }).first().click();
    await expect(page).toHaveURL(/\/medications\/methylphenidate/);
    await expect(page.getByRole('heading', { level: 1, name: 'Methylphenidate' })).toBeVisible();
  });

  test('shows the clinical fields grouped by area', async ({ page }) => {
    await page.goto('/medications/methylphenidate');
    for (const heading of ['Identity', 'Classification', 'Administration', 'Dosing',
                           'Pharmacokinetics', 'Safety', 'Monitoring']) {
      await expect(page.getByRole('heading', { level: 2, name: heading })).toBeVisible();
    }
    await expect(page.getByText('Dopamine and norepinephrine transporter (DAT&NET) agonist')).toBeVisible();
    await expect(page.getByText('Immediate-release tablet')).toBeVisible();
  });

  test('keeps source text verbatim rather than tidying units', async ({ page }) => {
    await page.goto('/medications/methylphenidate');
    await expect(page.getByText('2mg/kg/day', { exact: true })).toBeVisible();
  });

  test('says "Not supplied" instead of leaving a field blank', async ({ page }) => {
    await page.goto('/medications/methylphenidate');
    const monitoring = page.locator('.field-row').filter({ hasText: 'Monitoring tests' });
    await expect(monitoring.getByText('Not supplied')).toBeVisible();
    await expect(monitoring.getByText('Not applicable')).toHaveCount(0);
  });

  test('shows the source attached to a clinical claim', async ({ page }) => {
    await page.goto('/medications/sertraline');
    await expect(page.getByText('Summary of Product Characteristics').first()).toBeVisible();
    await expect(page.getByText(/Jurisdiction: IL/).first()).toBeVisible();
  });

  test('states the validation status in the record footer, not over the content', async ({ page }) => {
    await page.goto('/medications/sertraline');

    const footer = page.locator('.record-footer');
    await expect(footer).toContainText(/Validation status/i);
    await expect(footer).toContainText(/Version/i);

    // The heading area carries the medication, not its governance state.
    await expect(page.locator('.detail-header')).not.toContainText(/Validation status/i);
  });

  test('does not show editorial detail to a physician', async ({ page }) => {
    await page.goto('/medications/sertraline');
    // Which checks are outstanding is for the people who can act on them.
    await expect(page.getByText(/Outstanding checks/i)).toHaveCount(0);
    await expect(page.getByText(/carries a clinical claim with no source/i)).toHaveCount(0);
  });

  test('is deep-linkable and survives a reload', async ({ page }) => {
    await page.goto('/medications/fluoxetine');
    await page.reload();
    await expect(page.getByRole('heading', { level: 1, name: 'Fluoxetine' })).toBeVisible();
  });

  test('goes back to the results', async ({ page }) => {
    await search(page, 'fluoxetine');
    await page.getByRole('link', { name: /Fluoxetine/ }).first().click();
    await page.getByRole('link', { name: /back to results/i }).click();
    await expect(page.getByRole('region', { name: /search results/i })).toBeVisible();
  });
});

test.describe('comparison', () => {
  test('compares two medications side by side', async ({ page }) => {
    await page.locator('.result-row').filter({ hasText: 'Sertraline' })
      .getByRole('checkbox').check();
    await page.locator('.result-row').filter({ hasText: 'Fluoxetine' })
      .getByRole('checkbox').check();

    await page.getByRole('button', { name: /compare selected/i }).click();
    await expect(page).toHaveURL(/\/compare/);

    const table = page.getByRole('table');
    await expect(table.getByRole('columnheader', { name: 'Sertraline' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Fluoxetine' })).toBeVisible();
    await expect(table.getByRole('rowheader', { name: 'Dose range' })).toBeVisible();
  });

  test('caps the selection at three medications', async ({ page }) => {
    const rows = page.locator('.result-row');
    const count = await rows.count();
    for (let i = 0; i < Math.min(count, 4); i++) {
      const box = rows.nth(i).getByRole('checkbox');
      if (await box.isDisabled()) break;
      await box.check();
    }
    await expect(page.locator('.chip')).toHaveCount(3);
    // Any remaining checkbox is now disabled.
    await expect(rows.nth(3).getByRole('checkbox')).toBeDisabled();
  });

  test('needs at least two selections before comparing', async ({ page }) => {
    await page.locator('.result-row').first().getByRole('checkbox').check();
    await expect(page.getByRole('button', { name: /compare selected/i })).toBeDisabled();
  });

  test('shows only clinically comparable fields', async ({ page }) => {
    await page.goto('/compare?slugs=sertraline,fluoxetine');
    const table = page.getByRole('table');
    await expect(table.getByRole('rowheader', { name: 'Maximum dose' })).toBeVisible();
    // Long prose is deliberately excluded from the comparison.
    await expect(table.getByRole('rowheader', { name: 'Clinical notes' })).toHaveCount(0);
    await expect(table.getByRole('rowheader', { name: 'Adult indications' })).toHaveCount(0);
  });
});
