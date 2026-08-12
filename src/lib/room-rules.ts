import { heWord, heStartsWith } from "./hebrew";

/**
 * When the assistant speaks in the shared room.
 *
 * Its own module, with no database or agent imports, so the browser can use the
 * exact same function the server does. The typing indicator and the actual
 * behaviour cannot drift apart, because there is only one rule.
 *
 * Conservative on purpose. Two people brainstorming ask each other plenty of
 * questions; treating every "?" as a cue would make the assistant constantly
 * butt in. It speaks when named, when given an instruction, or when someone
 * taps the ask button — and otherwise listens.
 */

const HE_COMMANDS = heStartsWith(
  "תוסיף", "הוסף", "תזכיר", "תכין", "תבדוק", "תמצא", "תזמן",
  "תכתוב", "תרשום", "תיצור", "תראה", "קבע", "תסדר", "תארגן",
);
const HE_REQUESTS = heStartsWith("אפשר", "תוכל", "תוכלי");

const EN_COMMANDS =
  /^(add|remind|create|book|plan|find|check|schedule|draft|track|remember|order|list|show|set)\b/;

export function shouldRespond(text: string): boolean {
  const t = text.trim().toLowerCase();

  // Named directly.
  if (/(^|\s)@(ai|assistant|beitenu|claude)\b/.test(t)) return true;
  if (/\b(assistant|beitenu)\b/.test(t)) return true;
  if (heWord("עוזר").test(text) || heWord("ביתנו").test(text)) return true;

  // Given an instruction.
  if (EN_COMMANDS.test(t)) return true;
  if (HE_COMMANDS.test(text)) return true;

  // "can you ..." style requests.
  if (/^(can|could|would) you\b/.test(t)) return true;
  if (HE_REQUESTS.test(text)) return true;

  return false;
}
