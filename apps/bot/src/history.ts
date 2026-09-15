import { appendFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import type pino from 'pino';
import { config } from './config.js';
import type { ChatTurn } from './claude.js';

// One rolling window per chat, in memory only — restarting the container
// starts a fresh conversation. The durable record is the markdown log
// below, which is what Ishay and Claude Code actually read back later.
const sessions = new Map<number, ChatTurn[]>();

export function getHistory(chatId: number): ChatTurn[] {
  return sessions.get(chatId) ?? [];
}

export function pushTurn(chatId: number, turn: ChatTurn): void {
  const turns = sessions.get(chatId) ?? [];
  turns.push(turn);
  // Keep the last N exchanges (2 messages each) so the prompt sent to
  // Claude does not grow without bound over a long-running conversation.
  const maxMessages = config.historyTurns * 2;
  if (turns.length > maxMessages) {
    turns.splice(0, turns.length - maxMessages);
  }
  sessions.set(chatId, turns);
}

let logReady: Promise<void> | null = null;

async function ensureLogFile(logger: pino.Logger): Promise<void> {
  if (!logReady) {
    logReady = mkdir(dirname(config.logFile), { recursive: true })
      .then(() => undefined)
      .catch((err) => {
        logger.warn({ err: String(err) }, 'could not create directory for conversation log');
      });
  }
  await logReady;
}

// Appended, never rewritten: a plain, chronological transcript that reads
// fine on its own and that a later Claude Code session can just cat.
export async function logExchange(
  logger: pino.Logger,
  chatId: number,
  senderName: string,
  userText: string,
  assistantText: string,
): Promise<void> {
  await ensureLogFile(logger);
  const timestamp = new Date().toISOString();
  const entry =
    `\n## ${timestamp} — chat ${chatId} (${senderName})\n\n` +
    `**${senderName}:** ${userText}\n\n` +
    `**Claude:** ${assistantText}\n`;
  try {
    await appendFile(config.logFile, entry, 'utf8');
  } catch (err) {
    logger.warn({ err: String(err) }, 'could not append to conversation log');
  }
}
