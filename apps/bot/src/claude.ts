import Anthropic from '@anthropic-ai/sdk';
import { config } from './config.js';

export type ChatTurn = { role: 'user' | 'assistant'; content: string };

const client = new Anthropic({ apiKey: config.anthropicApiKey });

const SYSTEM_PREAMBLE = `You are Claude, in a private Telegram chat that is part of the Medication
Catalogue project — a bilingual (Hebrew/English) medication reference for
psychiatrists, built by Ishay Domb. You are talking with Liran Kor, a
clinical reviewer on the project, about the product: its clinical content,
data model, and the roadmap proposals below.

You are a separate conversation from the Claude Code session that writes and
ships the actual code — you cannot edit files, run commands, or deploy
anything here. Your job is to discuss the product with Liran, answer
questions about how it works today (grounded strictly in the documents
below, never guessed), think through her clinical and product feedback with
her, and help her turn it into something specific and actionable. Every
message in this chat is saved to a log Ishay and Claude Code read later, so
write as if it will be relayed — be concrete, not just conversational.

Reply in whichever language the message was written in (Hebrew or English).
If something isn't covered by the documents below, say so plainly rather
than inventing detail — this is a clinical-safety-sensitive project and
silent guessing is exactly the failure mode it's designed to avoid.

--- Project documents ---
{{CONTEXT}}
--- End of project documents ---`;

export async function askClaude(context: string, history: ChatTurn[]): Promise<string> {
  const system = SYSTEM_PREAMBLE.replace('{{CONTEXT}}', context || '(no context files loaded)');

  const response = await client.messages.create({
    model: config.anthropicModel,
    max_tokens: 1500,
    system,
    messages: history.map((turn) => ({ role: turn.role, content: turn.content })),
  });

  return response.content
    .filter((block): block is Anthropic.TextBlock => block.type === 'text')
    .map((block) => block.text)
    .join('\n')
    .trim();
}
