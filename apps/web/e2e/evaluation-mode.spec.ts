import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { STATE_FILES } from './helpers.ts';

/**
 * Evaluation mode: the catalogue may hold records published before clinical
 * review. The point of these tests is that this can never be quiet — the
 * banner, the badge and the per-record warning all have to appear.
 *
 * The setting is global, so each test restores it.
 */
test.describe('evaluation mode', () => {
  test.use({ storageState: STATE_FILES.admin });

  async function setAllowance(request: import('@playwright/test').APIRequestContext, value: boolean) {
    const res = await request.put('/api/settings/publication.allow_unvalidated', {
      data: { value },
    });
    expect(res.ok()).toBe(true);
  }

  test.afterEach(async ({ request }) => {
    await setAllowance(request, false);
  });

  test('shows a banner on every screen while it is on', async ({ page, request }) => {
    await setAllowance(request, true);

    await page.goto('/');
    const banner = page.locator('.evaluation-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/do not use it for clinical decisions/i);

    // Present on the detail page too, not only the list.
    await page.goto('/medications/sertraline');
    await expect(page.locator('.evaluation-banner')).toBeVisible();
  });

  test('hides the banner once it is switched off', async ({ page, request }) => {
    await setAllowance(request, false);
    await page.goto('/');
    await expect(page.locator('.evaluation-banner')).toHaveCount(0);
  });

  test('the banner meets the accessibility checks', async ({ page, request }) => {
    await setAllowance(request, true);
    await page.goto('/');
    await expect(page.locator('.evaluation-banner')).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test('only an administrator can switch it on', async ({ browser }) => {
    const context = await browser.newContext({ storageState: STATE_FILES.editor });
    const res = await context.request.put('/api/settings/publication.allow_unvalidated', {
      data: { value: true },
    });
    expect(res.status()).toBe(403);
    await context.close();
  });
});
