import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type pino from 'pino';
import { config } from './config.js';

// The bot's only knowledge of the project is whatever plain-text context we
// hand it here — it is a fresh Claude API call on every message, not this
// Claude Code session. Loading real project docs (not a hand-written blurb)
// keeps that knowledge from drifting out of sync with what the app actually
// does; when the docs are updated, a redeploy is enough to refresh the bot.
export function loadContext(logger: pino.Logger): string {
  const sections: string[] = [];
  let totalChars = 0;

  for (const file of config.contextFiles) {
    const path = join(config.contextDir, file);
    try {
      const text = readFileSync(path, 'utf8');
      if (totalChars + text.length > config.maxContextChars) {
        logger.warn({ file }, 'skipping context file: would exceed BOT_MAX_CONTEXT_CHARS');
        continue;
      }
      sections.push(`<doc path="${file}">\n${text}\n</doc>`);
      totalChars += text.length;
    } catch (err) {
      logger.warn({ file, err: String(err) }, 'context file not found, skipping');
    }
  }

  logger.info({ files: sections.length, totalChars }, 'loaded bot context');

  return sections.join('\n\n');
}
