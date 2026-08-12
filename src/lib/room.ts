import { all, one, run, json, logActivity } from "./db";
import { ask } from "./agent";
import { shouldRespond } from "./room-rules";

export { shouldRespond };

/**
 * "Think together" — one shared thread Ishay and Liran both post into, with the
 * assistant sitting in as a third participant.
 *
 * The design problem is not the plumbing, it's knowing when to speak. An
 * assistant that answers every message turns a conversation between two people
 * into a conversation refereed by a bot, and they stop using it. So the default
 * is silence: it reads everything, and speaks only when actually addressed.
 *
 * It is never idle though — passive capture still runs, so facts mentioned in
 * passing are remembered even when nothing is said back.
 */

export const ROOM_KEY = "household";

export interface RoomMessage {
  id: number;
  role: string;
  content: string;
  speaker: string | null;
  speaker_key: string | null;
  color: string | null;
  created_at: string;
}

export function getRoom(): number {
  const existing = one<{ id: number }>(`SELECT id FROM conversations WHERE room_key = ?`, [
    ROOM_KEY,
  ]);
  if (existing) return existing.id;

  const res = run(
    `INSERT INTO conversations (title, channel, kind, room_key)
     VALUES ('Think together', 'web', 'room', ?)`,
    [ROOM_KEY],
  );
  return res.lastInsertRowid as number;
}

export function roomMessages(sinceId = 0, limit = 200): RoomMessage[] {
  return all<RoomMessage>(
    `SELECT m.id, m.role, m.content, m.created_at,
            p.name AS speaker, p.key AS speaker_key, p.color
     FROM messages m
     LEFT JOIN people p ON p.id = m.person_id
     WHERE m.conversation_id = ? AND m.id > ? AND m.content IS NOT NULL
     ORDER BY m.id LIMIT ?`,
    [getRoom(), sinceId, limit],
  );
}

export interface PostResult {
  posted: RoomMessage[];
  assistantReplied: boolean;
}

/**
 * Post a human message to the room, and let the assistant answer only if it was
 * addressed (or if `askAssistant` forces it, from the "ask" button).
 */
export async function postToRoom(input: {
  text: string;
  actor: string;
  askAssistant?: boolean;
}): Promise<PostResult> {
  const roomId = getRoom();
  const text = input.text.trim();
  if (!text) throw new Error("empty message");

  const respond = input.askAssistant || shouldRespond(text);

  if (!respond) {
    // Just record it. The assistant stays out of the way, but the message is
    // still in the thread it will read next time it does speak.
    const res = run(
      `INSERT INTO messages (conversation_id, role, channel, content, person_id)
       VALUES (?, 'user', 'room', ?, (SELECT id FROM people WHERE key = ?))`,
      [roomId, text, input.actor],
    );
    run(`UPDATE conversations SET updated_at = datetime('now') WHERE id = ?`, [roomId]);
    return {
      posted: roomMessages((res.lastInsertRowid as number) - 1),
      assistantReplied: false,
    };
  }

  // ask() writes both the user message and the reply into this conversation.
  const before =
    one<{ id: number }>(`SELECT MAX(id) AS id FROM messages WHERE conversation_id = ?`, [roomId])
      ?.id ?? 0;

  await ask({ message: text, conversationId: roomId, actor: input.actor, channel: "room" });

  return { posted: roomMessages(before), assistantReplied: true };
}

/**
 * Turn a stretch of discussion into decisions and tasks. This is the point of
 * brainstorming together — the thread should leave something behind.
 */
export async function summariseRoom(actor: string, lastN = 40): Promise<string> {
  const roomId = getRoom();
  const recent = all<{ n: number }>(
    `SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?`,
    [roomId],
  )[0].n;
  if (recent === 0) return "Nothing discussed yet.";

  const reply = await ask({
    message:
      `Read back over the last ${lastN} messages in this room and turn the discussion into ` +
      `something durable. Create tasks for anything we agreed to do, with owners where we ` +
      `said who. Remember any facts that came up. If we settled on something big, consider ` +
      `set_focus. Then reply with a short summary: what we decided, and what you created. ` +
      `Do not invent agreements we did not reach — if we went round in circles, say so.`,
    conversationId: roomId,
    actor,
    channel: "room",
  });

  logActivity({
    actor,
    action: "summarised_room",
    summary: "Turned the shared discussion into tasks",
  });
  return reply.text;
}

export function roomStats() {
  const roomId = getRoom();
  return {
    messages: all<{ n: number }>(
      `SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?`,
      [roomId],
    )[0].n,
    lastActivity: one<{ updated_at: string }>(
      `SELECT updated_at FROM conversations WHERE id = ?`,
      [roomId],
    )?.updated_at,
  };
}

export { json };
