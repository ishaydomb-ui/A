import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { resolve } from 'node:path';
import { E2E_APP_SECRET, E2E_DATABASE_URL } from '../playwright.config.ts';

const exec = promisify(execFile);
const API_DIR = resolve(import.meta.dirname, '../../api');

/**
 * Resets the end-to-end database to a known state: schema migrated, the
 * website-snapshot workbook imported as drafts, a handful of records taken
 * through review to published, and one account per role.
 */
export default async function globalSetup(): Promise<void> {
  const env = {
    ...process.env,
    NODE_ENV: 'test',
    DATABASE_URL: E2E_DATABASE_URL,
    APP_SECRET: E2E_APP_SECRET,
    IMPORT_STORAGE_DIR: './var/e2e-imports',
    LOG_LEVEL: 'silent',
  };

  const run = async (script: string, args: string[] = [], extra: Record<string, string> = {}) => {
    const { stdout, stderr } = await exec('node', ['--import', 'tsx', script, ...args], {
      cwd: API_DIR,
      env: { ...env, ...extra },
      maxBuffer: 16 * 1024 * 1024,
    });
    if (process.env.E2E_VERBOSE) console.log(stdout || stderr);
  };

  await run('src/scripts/e2e-reset.ts');
}
