import { expect, test } from '@playwright/test';
import { STATE_FILES } from './helpers.ts';

test.use({ storageState: STATE_FILES.physician });

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
});

test.describe('explore (home)', () => {
  test('searching from the hero goes to the catalogue with the query', async ({ page }) => {
    const box = page.getByRole('combobox');
    await box.fill('sertraline');
    await box.press('Enter');
    await expect(page).toHaveURL(/\/catalogue\?q=sertraline/);
    await expect(page.getByText('Sertraline').first()).toBeVisible();
  });

  test('a clinical-area tile opens the catalogue filtered to that group', async ({ page }) => {
    const tile = page.locator('.area-tile').first();
    const name = (await tile.locator('.area-tile-name').innerText()).trim();
    await tile.click();
    await expect(page).toHaveURL(/\/catalogue\?therapeuticGroup=/);
    await expect(page.locator('.active-filter-chip').first()).toContainText(name);
  });

  test('view all opens the full, unfiltered catalogue', async ({ page }) => {
    await page.getByRole('link', { name: /view all/i }).click();
    await expect(page).toHaveURL(/\/catalogue$/);
    await expect(page.locator('.result-row').first()).toBeVisible();
  });

  test('browse the full catalogue link works from the foot of the page', async ({ page }) => {
    await page.getByRole('link', { name: /browse the full catalogue/i }).click();
    await expect(page).toHaveURL(/\/catalogue$/);
  });

  test('a medication visited from the catalogue appears in Recently viewed', async ({ page }) => {
    await expect(page.getByText(/recently viewed/i)).toHaveCount(0);

    await page.goto('/medications/sertraline');
    await expect(page.getByRole('heading', { level: 1, name: 'Sertraline' })).toBeVisible();

    await page.goto('/');
    const recent = page.getByRole('heading', { name: /recently viewed/i });
    await expect(recent).toBeVisible();
    await expect(page.getByRole('link', { name: /Sertraline/ })).toBeVisible();
  });
});

test.describe('saved medications', () => {
  test('saving from a medication page adds it to Saved and back again removes it', async ({ page }) => {
    await page.goto('/medications/sertraline');
    const toggle = page.getByRole('button', { name: /^save$/i });
    await toggle.click();
    await expect(page.getByRole('button', { name: /remove from saved/i })).toBeVisible();

    await page.goto('/saved');
    await expect(page.getByRole('link', { name: /Sertraline/ })).toBeVisible();

    await page
      .locator('.result-row')
      .filter({ hasText: 'Sertraline' })
      .getByRole('button', { name: /remove from saved/i })
      .click();
    await expect(page.getByText(/nothing saved yet/i)).toBeVisible();
  });

  test('the saved list is empty by default, with a helpful hint', async ({ page }) => {
    await page.goto('/saved');
    await expect(page.getByText(/nothing saved yet/i)).toBeVisible();
    await expect(page.getByText(/save a medication from its page/i)).toBeVisible();
  });
});
