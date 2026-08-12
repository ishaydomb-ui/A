import Anthropic from "@anthropic-ai/sdk";
import { all, run, json } from "../db";
import { buildSystemPrompt } from "./prompt";
import { toolSpecs, runTool, type ToolContext } from "./tools";

/**
 * The agent loop. One entry point for every channel - web chat, WhatsApp,
 * a voice note, or an automation firing on a schedule. They all end up here,
 * so behaviour is identical no matter how the request arrived.
 */

const MAX_TURNS = 12;

let _client: Anthropic | null = null;
function client(): Anthropic {
  if (!_client) {
    const apiKey = process.env.ANTHROPIC_API_KEY;
    if (!apiKey) throw new Error("ANTHROPIC_API_KEY is not set");
    _client = new Anthropic({ apiKey });
  }
  return _client;
}

export interface AgentReply {
  text: string;
  toolsUsed: string[];
  conversationId: number;
}

export async function ask(input: {
  message: string;
  conversationId?: number;
  actor?: string;
  channel?: string;
}): Promise<AgentReply> {
  const actor = input.actor ?? "ishay";
  const channel = input.channel ?? "web";
  const conversationId = input.conversationId ?? startConversation(actor, channel);

  run(
    `INSERT INTO messages (conversation_id, role, channel, content, person_id)
     VALUES (?, 'user', ?, ?, (SELECT id FROM people WHERE key = ?))`,
    [conversationId, channel, input.message, actor],
  );

  const messages: Anthropic.MessageParam[] = loadHistory(conversationId);
  const ctx: ToolContext = { actor, channel };
  const toolsUsed: string[] = [];
  let finalText = "";

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    const response = await client().messages.create({
      model: process.env.AGENT_MODEL || "claude-sonnet-5",
      max_tokens: 4096,
      system: buildSystemPrompt({ actor, channel }),
      tools: toolSpecs(),
      messages,
    });

    const textParts = response.content
      .filter((b): b is Anthropic.TextBlock => b.type === "text")
      .map((b) => b.text);
    if (textParts.length) finalText = textParts.join("\n").trim();

    const toolUses = response.content.filter(
      (b): b is Anthropic.ToolUseBlock => b.type === "tool_use",
    );

    if (!toolUses.length) {
      messages.push({ role: "assistant", content: response.content });
      break;
    }

    messages.push({ role: "assistant", content: response.content });

    const results: Anthropic.ToolResultBlockParam[] = [];
    for (const use of toolUses) {
      toolsUsed.push(use.name);
      const { ok, result } = await runTool(use.name, use.input, ctx);
      results.push({
        type: "tool_result",
        tool_use_id: use.id,
        content: JSON.stringify(result ?? null).slice(0, 60_000),
        is_error: !ok,
      });
    }
    messages.push({ role: "user", content: results });
  }

  run(
    `INSERT INTO messages (conversation_id, role, channel, content, tool_calls)
     VALUES (?, 'assistant', ?, ?, ?)`,
    [conversationId, channel, finalText, JSON.stringify(toolsUsed)],
  );
  run(`UPDATE conversations SET updated_at = datetime('now') WHERE id = ?`, [conversationId]);

  return { text: finalText, toolsUsed, conversationId };
}

function startConversation(actor: string, channel: string): number {
  const res = run(
    `INSERT INTO conversations (channel, person_id)
     VALUES (?, (SELECT id FROM people WHERE key = ?))`,
    [channel, actor],
  );
  return res.lastInsertRowid as number;
}

/**
 * Recent history only. A household assistant rarely needs deep scrollback -
 * durable context lives in the database and the system prompt, not in the
 * message log. This keeps every request cheap and fast.
 */
function loadHistory(conversationId: number, limit = 20): Anthropic.MessageParam[] {
  const rows = all<{ role: string; content: string | null }>(
    `SELECT role, content FROM messages
     WHERE conversation_id = ? AND role IN ('user','assistant') AND content IS NOT NULL
     ORDER BY id DESC LIMIT ?`,
    [conversationId, limit],
  ).reverse();

  return rows
    .filter((r) => r.content && r.content.trim())
    .map((r) => ({
      role: r.role === "user" ? ("user" as const) : ("assistant" as const),
      content: r.content!,
    }));
}

export function listConversations(limit = 20) {
  return all(
    `SELECT c.id, c.title, c.channel, c.updated_at,
            (SELECT content FROM messages m WHERE m.conversation_id = c.id
              AND m.role='user' ORDER BY m.id LIMIT 1) AS first_message
     FROM conversations c ORDER BY c.updated_at DESC LIMIT ?`,
    [limit],
  );
}

export function conversationMessages(id: number) {
  return all<{ id: number; role: string; content: string; tool_calls: string; created_at: string }>(
    `SELECT id, role, content, tool_calls, created_at FROM messages
     WHERE conversation_id = ? ORDER BY id`,
    [id],
  ).map((m) => ({ ...m, tool_calls: json<string[]>(m.tool_calls, []) }));
}
