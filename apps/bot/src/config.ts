// Environment is validated once, at start-up, so a missing secret fails
// loudly before the bot ever tries to poll Telegram — not on the first
// message someone happens to send.

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set. See docs/telegram-bot.md for setup.`);
  }
  return value;
}

function parseChatIds(raw: string | undefined): Set<number> {
  if (!raw) return new Set();
  return new Set(
    raw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => Number(s))
      .filter((n) => Number.isInteger(n)),
  );
}

export const config = {
  telegramBotToken: required('TELEGRAM_BOT_TOKEN'),
  anthropicApiKey: required('ANTHROPIC_API_KEY'),
  anthropicModel: process.env.ANTHROPIC_MODEL || 'claude-sonnet-5',
  // Empty on purpose is allowed: the bot still runs so /start can hand the
  // owner the chat id to add, but no one gets a Claude-backed reply yet.
  allowedChatIds: parseChatIds(process.env.BOT_ALLOWED_CHAT_IDS),
  contextDir: process.env.BOT_CONTEXT_DIR || new URL('../../../docs', import.meta.url).pathname,
  contextFiles: (
    process.env.BOT_CONTEXT_FILES ||
    'technical-capabilities.md,roadmap-proposals.md,decisions.md,review-and-publish.md,import-guide.md,architecture.md'
  )
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
  maxContextChars: Number(process.env.BOT_MAX_CONTEXT_CHARS || 200_000),
  historyTurns: Number(process.env.BOT_HISTORY_TURNS || 20),
  logFile: process.env.BOT_LOG_FILE || '/data/conversation-log.md',
  logLevel: process.env.LOG_LEVEL || 'info',
};
