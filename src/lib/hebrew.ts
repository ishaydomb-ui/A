/**
 * Hebrew-safe word matching.
 *
 * JavaScript's \b is defined over ASCII word characters, so /\bחוג\b/ and
 * /^אפשר\b/ never match: Hebrew letters aren't \w, so the boundary assertion
 * fails on both sides. This silently breaks every Hebrew keyword rule while
 * looking completely correct, and it has already caught me twice — once in
 * calendar classification, once in the shared room. Hence one helper, in one
 * place, used everywhere Hebrew is matched.
 *
 * Hebrew also glues single-letter prefixes onto words (ל, ב, ה, מ, ש, ו, כ), so
 * "לחוג" has to read as "חוג".
 */

/** Characters that count as a word edge either side of a Hebrew token. */
export const HE_EDGE = `[\\s\\-־,.:;!?"'()\\[\\]/]`;

const PREFIXES = "[בלמהשוכ]?";

/** Whole-word match anywhere in the text, prefixes allowed. */
export function heWord(word: string): RegExp {
  return new RegExp(`(^|${HE_EDGE})${PREFIXES}${escape(word)}(${HE_EDGE}|$)`);
}

/** Same, but anchored to the start — for "does this message open with a command". */
export function heStartsWith(...words: string[]): RegExp {
  return new RegExp(`^\\s*${PREFIXES}(${words.map(escape).join("|")})(${HE_EDGE}|$)`);
}

/** True if the string contains Hebrew letters at all. */
export function isHebrew(text: string): boolean {
  return /[֐-׿]/.test(text);
}

function escape(word: string): string {
  return word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
