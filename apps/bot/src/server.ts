import { Bot, Context } from 'grammy';
import pino from 'pino';
import { config } from './config.js';
import { loadContext } from './context.js';
import { askClaude } from './claude.js';
import { getHistory, pushTurn, logExchange } from './history.js';

const logger = pino({ level: config.logLevel });

const context = loadContext(logger);
if (config.allowedChatIds.size === 0) {
  logger.warn(
    'BOT_ALLOWED_CHAT_IDS is empty — no one will get a Claude reply yet. ' +
      'Message the bot with /start to learn a chat id to allow, then set it and redeploy.',
  );
}

const bot = new Bot(config.telegramBotToken);

const WHOAMI_REPLY = (chatId: number) =>
  `שלום! מזהה הצ'אט הזה הוא: ${chatId}\n\n` +
  `שלחו את המספר הזה למי שמנהל את הבוט כדי לקבל גישה. ` +
  `ברגע שהוא יתווסף לרשימת המורשים, אפשר לכתוב כאן כרגיל.\n\n` +
  `Hi! This chat's id is: ${chatId}\nSend that number to whoever administers ` +
  `this bot to be granted access.`;

bot.command('start', async (ctx) => {
  await ctx.reply(WHOAMI_REPLY(ctx.chat.id));
});

bot.command('whoami', async (ctx) => {
  await ctx.reply(WHOAMI_REPLY(ctx.chat.id));
});

// Telegram caps a single message at 4096 characters; longer replies are
// split on paragraph breaks rather than truncated.
async function replyInChunks(ctx: Context, text: string): Promise<void> {
  const LIMIT = 4000;
  if (text.length <= LIMIT) {
    await ctx.reply(text);
    return;
  }
  let remaining = text;
  while (remaining.length > 0) {
    let cut = remaining.length > LIMIT ? remaining.lastIndexOf('\n\n', LIMIT) : remaining.length;
    if (cut <= 0) cut = Math.min(LIMIT, remaining.length);
    await ctx.reply(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
}

bot.on('message:text', async (ctx) => {
  const chatId = ctx.chat.id;
  const text = ctx.message.text;

  if (!config.allowedChatIds.has(chatId)) {
    logger.info({ chatId }, 'message from a chat id not on the allowlist');
    await ctx.reply(WHOAMI_REPLY(chatId));
    return;
  }

  const senderName =
    [ctx.from?.first_name, ctx.from?.last_name].filter(Boolean).join(' ') ||
    ctx.from?.username ||
    'Liran';

  await ctx.replyWithChatAction('typing');
  pushTurn(chatId, { role: 'user', content: text });

  try {
    const reply = await askClaude(context, getHistory(chatId));
    pushTurn(chatId, { role: 'assistant', content: reply });
    await replyInChunks(ctx, reply);
    await logExchange(logger, chatId, senderName, text, reply);
  } catch (err) {
    logger.error({ err: String(err), chatId }, 'Claude call failed');
    await ctx.reply(
      'משהו נכשל בצד שלי ולא הצלחתי לענות. ההודעה שלך לא אבדה — אפשר לנסות שוב עוד רגע.',
    );
  }
});

bot.catch((err) => {
  logger.error({ err: String(err.error) }, 'unhandled error in bot update');
});

// bot.stop() must be awaited before the process exits: it is what tells
// Telegram to cleanly cancel the in-flight long-poll request. Exiting
// without waiting for it (the previous bug here) leaves that request
// dangling from Telegram's side, so the next container start conflicts with
// it — a 409 "terminated by other getUpdates request" that can persist for
// a long time, since Telegram has no clean disconnect to notice.
async function shutdown(signal: NodeJS.Signals): Promise<void> {
  logger.info({ signal }, 'shutting down');
  await bot.stop();
  process.exit(0);
}

process.once('SIGINT', () => void shutdown('SIGINT'));
process.once('SIGTERM', () => void shutdown('SIGTERM'));

logger.info({ allowedChatIds: [...config.allowedChatIds] }, 'starting bot (long polling)');
await bot.start();
