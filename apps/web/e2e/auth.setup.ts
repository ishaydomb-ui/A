import { test as setup } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { ACCOUNTS, STATE_FILES, signIn, signInAsAdmin } from './helpers.ts';

/**
 * Signs in once per role and saves the session, so the rest of the suite
 * reuses it.
 *
 * This mirrors how the system is really used — a clinician signs in once and
 * works — and it keeps the suite from tripping the login rate limiter, which
 * is a feature rather than something to work around.
 */
setup('authenticate every role', async ({ browser }) => {
  await mkdir('.auth', { recursive: true });

  for (const role of ['physician', 'editor', 'reviewer'] as const) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await signIn(page, role);
    await context.storageState({ path: STATE_FILES[role] });
    await context.close();
  }

  // The administrator additionally completes the mandatory MFA enrolment.
  const context = await browser.newContext();
  const page = await context.newPage();
  await signInAsAdmin(page);
  await context.storageState({ path: STATE_FILES.admin });
  await context.close();

  void ACCOUNTS;
});
