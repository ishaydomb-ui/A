import { expect, test } from '@playwright/test';
import { STATE_FILES, openFilters, search, setLocale } from './helpers.ts';

// Reuses the physician session established by the setup project.
test.use({ storageState: STATE_FILES.physician });

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.result-row').first()).toBeVisible();
});

test.describe('search', () => {
  test('finds a medication by generic name', async ({ page }) => {
    await search(page, 'sertraline');
    const results = page.getByRole('region', { name: /search results/i });
    await expect(results.getByRole('link', { name: /Sertraline/ }).first()).toBeVisible();
  });

  test('finds a medication by trade name', async ({ page }) => {
    await search(page, 'ritalin');
    await expect(page.getByText('Methylphenidate').first()).toBeVisible();
  });

  test('tolerates a misspelling', async ({ page }) => {
    await search(page, 'sertrline');
    await expect(page.getByRole('status').filter({ hasText: /closest results/i })).toBeVisible();
    await expect(page.getByText('Sertraline').first()).toBeVisible();
  });

  test('shows a helpful empty state for a query that matches nothing', async ({ page }) => {
    await search(page, 'zzzqqqxxx');
    await expect(page.getByText(/no medications matched/i)).toBeVisible();
    await expect(page.getByText(/check the spelling/i)).toBeVisible();
  });

  test('highlights the matched text', async ({ page }) => {
    await search(page, 'sertra');
    await expect(page.locator('mark').first()).toHaveText(/sertra/i);
  });

  test('offers autocomplete suggestions and accepts one', async ({ page }) => {
    const box = page.getByRole('combobox');
    await box.fill('sertr');
    const option = page.getByRole('option').first();
    await expect(option).toBeVisible();
    await option.click();
    await expect(page.getByText('Sertraline').first()).toBeVisible();
  });

  test('keeps the query in the URL so a search can be shared', async ({ page }) => {
    await search(page, 'sertraline');
    await expect(page).toHaveURL(/q=sertraline/);
    await page.reload();
    await expect(page.getByText('Sertraline').first()).toBeVisible();
  });

  test('filters by therapeutic group', async ({ page }) => {
    await openFilters(page);
    const antidepressants = page.getByRole('checkbox', { name: /Antidepressants/i }).first();
    await antidepressants.check();
    await expect(page).toHaveURL(/therapeuticGroup=Antidepressants/);
    await expect(page.getByText('Methylphenidate')).toHaveCount(0);
  });

  test('results are a compact list, not one card per medication', async ({ page }) => {
    const rows = page.locator('.result-row');
    await expect(rows.first()).toBeVisible();
    const box = await rows.first().boundingBox();
    // A row, not a card: comfortably under 120px tall.
    expect(box!.height).toBeLessThan(120);
  });
});

test.describe('drafts are invisible to physicians', () => {
  test('an unpublished record is not in the results', async ({ page }) => {
    // Atomoxetine exists in the catalogue only as an unpublished draft. Any
    // hit here would have to be an approximate match on a published drug, so
    // the assertion is that the draft itself never appears.
    await search(page, 'atomoxetine');
    await expect(page.getByRole('link', { name: /atomoxetine/i })).toHaveCount(0);
    await expect(page.getByText('NRI', { exact: true })).toHaveCount(0);
  });

  test('navigating straight to an unpublished record shows not found', async ({ page }) => {
    await page.goto('/medications/nri');
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByText(/Atomoxetine/)).toHaveCount(0);
  });
});

test.describe('Hebrew interface', () => {
  test('switches language and direction', async ({ page }) => {
    await setLocale(page, 'he');
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.locator('html')).toHaveAttribute('lang', 'he');
    await expect(page.getByRole('heading', { level: 1, name: 'קטלוג' })).toBeVisible();
  });

  test('finds English-named records while the interface is in Hebrew', async ({ page }) => {
    await setLocale(page, 'he');
    await page.goto('/');
    await expect(page.locator('.result-row').first()).toBeVisible();
    await search(page, 'sertraline');
    await expect(page.getByText('Sertraline').first()).toBeVisible();
  });

  test('the language toggle switches direction both ways', async ({ page }) => {
    // The toggle's accessible name is itself localised, as it should be, so
    // the locator has to match it in either language.
    const toggle = page.getByRole('button', { name: /language|שפה/i });

    await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
    await toggle.click();
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.locator('html')).toHaveAttribute('lang', 'he');

    await toggle.click();
    await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  });

  test('the chosen language survives a reload', async ({ page }) => {
    await page.getByRole('button', { name: /language|שפה/i }).click();
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  });
});
