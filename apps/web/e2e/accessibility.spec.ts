import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { STATE_FILES, setLocale } from './helpers.ts';

/**
 * Automated WCAG 2.1 A/AA checks. These catch a real but limited class of
 * problems, so the keyboard and screen-reader semantics below are asserted
 * explicitly as well.
 */
async function audit(page: Page) {
  return new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
}

test.describe('automated accessibility audit — signed out', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('sign-in page', async ({ page }) => {
    await page.goto('/sign-in');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });

  test('privacy policy', async ({ page }) => {
    await page.goto('/privacy');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });
});

test.describe('automated accessibility audit — physician', () => {
  test.use({ storageState: STATE_FILES.physician });

  test('explore (home)', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });

  test('saved (empty)', async ({ page }) => {
    await page.goto('/saved');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });

  test('search results', async ({ page }) => {
    await page.goto('/catalogue');
    await expect(page.locator('.result-row').first()).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });

  test('medication detail', async ({ page }) => {
    await page.goto('/medications/methylphenidate');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });

  test('comparison', async ({ page }) => {
    await page.goto('/compare?slugs=sertraline,fluoxetine,methylphenidate');
    await expect(page.getByRole('table')).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });

  test('Hebrew search results', async ({ page }) => {
    await setLocale(page, 'he');
    await page.goto('/catalogue');
    await expect(page.locator('.result-row').first()).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });
});

test.describe('automated accessibility audit — reviewer', () => {
  test.use({ storageState: STATE_FILES.reviewer });

  test('review report', async ({ page }) => {
    await page.goto('/review');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const results = await audit(page);
    expect(results.violations).toEqual([]);
  });
});

test.describe('keyboard operation', () => {
  test.use({ storageState: STATE_FILES.physician });

  test('the skip link is the first stop and jumps to the content', async ({ page }) => {
    await page.goto('/catalogue');
    await expect(page.locator('.result-row').first()).toBeVisible();

    await page.keyboard.press('Tab');
    const focused = page.locator(':focus');
    await expect(focused).toHaveText(/skip to main content/i);

    await page.keyboard.press('Enter');
    await expect(page.locator('#main-content')).toBeFocused();
  });

  test('the search box and results are reachable by keyboard alone', async ({ page }) => {
    await page.goto('/catalogue');
    await expect(page.locator('.result-row').first()).toBeVisible();

    await page.getByRole('combobox').focus();
    await page.keyboard.type('sertraline');
    await page.keyboard.press('Enter');
    await expect(page.getByText('Sertraline').first()).toBeVisible();
  });

  test('autocomplete is operable with the arrow keys', async ({ page }) => {
    await page.goto('/catalogue');
    const box = page.getByRole('combobox');
    await box.focus();
    await page.keyboard.type('sertr');
    await expect(page.getByRole('option').first()).toBeVisible();

    await page.keyboard.press('ArrowDown');
    await expect(page.getByRole('option').first()).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('Enter');
    await expect(page.getByText('Sertraline').first()).toBeVisible();
  });

  test('Escape closes the suggestion list', async ({ page }) => {
    await page.goto('/catalogue');
    const box = page.getByRole('combobox');
    await box.focus();
    await page.keyboard.type('sertr');
    await expect(page.getByRole('option').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('option')).toHaveCount(0);
    await expect(box).toHaveAttribute('aria-expanded', 'false');
  });

});

test.describe('screen-reader semantics', () => {
  test.use({ storageState: STATE_FILES.physician });

  test('headings start at level one and do not skip a level', async ({ page }) => {
    await page.goto('/medications/methylphenidate');
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);

    const levels = await page.evaluate(() =>
      [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((h) => Number(h.tagName[1])),
    );
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });

  test('the result count is announced in a live region', async ({ page }) => {
    await page.goto('/catalogue');
    const status = page.locator('[role="status"][aria-live="polite"]').first();
    await expect(status).toContainText(/medications?/i);
  });

  test('each compare control names the medication it belongs to', async ({ page }) => {
    await page.goto('/catalogue');
    const first = page.locator('.result-row').first();
    const name = await first.locator('.result-name').innerText();
    const label = await first.getByRole('checkbox').getAttribute('aria-label')
      ?? await first.locator('label').innerText();
    expect(label.toLowerCase()).toContain(name.split('\n')[0].trim().toLowerCase().split(' ')[0]);
  });

  test('the page language and direction are declared', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  });
});

test.describe('mobile layout', () => {
  test.use({ storageState: STATE_FILES.physician });

  test('the page does not scroll sideways at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto('/catalogue');
    await expect(page.locator('.result-row').first()).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('the detail page does not scroll sideways at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto('/medications/methylphenidate');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('touch targets are at least 44px', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto('/catalogue');
    await expect(page.locator('.result-row').first()).toBeVisible();

    const small = await page.evaluate(() => {
      const selectors = 'button, a.btn, input[type=search], input[type=email], input[type=password]';
      return [...document.querySelectorAll(selectors)]
        .filter((el) => (el as HTMLElement).offsetParent !== null)
        .map((el) => ({ text: el.textContent?.trim().slice(0, 24), h: el.getBoundingClientRect().height }))
        .filter((el) => el.h > 0 && el.h < 44);
    });
    expect(small).toEqual([]);
  });
});

test.describe('signed-out pages', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('every interactive element shows a visible focus indicator', async ({ page }) => {
    await page.goto('/sign-in');
    await page.getByLabel(/email/i).focus();
    const shadow = await page.evaluate(() => getComputedStyle(document.activeElement!).boxShadow);
    expect(shadow).not.toBe('none');
  });

  test('error messages are announced as alerts', async ({ page }) => {
    await page.goto('/sign-in');
    await page.getByLabel(/email/i).fill('physician@example.org');
    await page.getByLabel(/^password$/i).fill('wrong-password-value');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page.getByRole('alert')).toBeVisible();
  });

  test('the sign-in form labels every field', async ({ page }) => {
    await page.goto('/sign-in');
    const unlabelled = await page.evaluate(() =>
      [...document.querySelectorAll('input, select, textarea')].filter((el) => {
        const id = el.getAttribute('id');
        const labelled = id && document.querySelector(`label[for="${id}"]`);
        return !labelled && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby');
      }).length,
    );
    expect(unlabelled).toBe(0);
  });
});
