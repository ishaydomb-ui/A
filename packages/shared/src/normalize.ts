/**
 * Search-text normalisation, shared by the indexer and the query parser so
 * that both sides always agree. Normalisation applies ONLY to the search
 * index — displayed clinical text is always the untouched original.
 */

/** Hebrew final forms mapped to their medial forms. */
const HEBREW_FINALS: Record<string, string> = {
  'ך': 'כ', // ך → כ
  'ם': 'מ', // ם → מ
  'ן': 'נ', // ן → נ
  'ף': 'פ', // ף → פ
  'ץ': 'צ', // ץ → צ
};

/** Niqqud, cantillation and other Hebrew combining marks. */
const HEBREW_MARKS = /[֑-ׇֽֿׁׂׅׄ]/g;
/** Geresh / gershayim, including the ASCII lookalikes used in practice. */
const HEBREW_PUNCT = /[׳״'"‘’“”]/g;
/** Latin combining diacritics, after NFD decomposition. */
const LATIN_MARKS = /[̀-ͯ]/g;

export function normalizeText(input: string): string {
  if (!input) return '';
  let s = input.normalize('NFD').replace(LATIN_MARKS, '');
  s = s.replace(HEBREW_MARKS, '');
  s = s.replace(HEBREW_PUNCT, '');
  s = s.replace(/[א-ת]/g, (c) => HEBREW_FINALS[c] ?? c);
  s = s.toLowerCase();
  // Any run of non-alphanumeric, non-Hebrew characters becomes a single space.
  s = s.replace(/[^0-9a-zא-ת]+/g, ' ');
  return s.trim();
}

export function tokenize(input: string): string[] {
  const n = normalizeText(input);
  return n ? n.split(' ').filter(Boolean) : [];
}

/**
 * Damerau-Levenshtein distance, capped for efficiency. Used to rank fuzzy
 * matches; the database does the candidate selection via trigram similarity.
 */
export function editDistance(a: string, b: string, max = 3): number {
  if (a === b) return 0;
  if (Math.abs(a.length - b.length) > max) return max + 1;
  const prev = new Array(b.length + 1);
  let prevPrev: number[] = [];
  let curr: number[] = [];
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  let last = prev;
  for (let i = 1; i <= a.length; i++) {
    curr = new Array(b.length + 1);
    curr[0] = i;
    let rowMin = curr[0];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(curr[j - 1] + 1, last[j] + 1, last[j - 1] + cost);
      // Transposition (Damerau): "sertraline" vs "sertraline"
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        v = Math.min(v, prevPrev[j - 2] + cost);
      }
      curr[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > max) return max + 1;
    prevPrev = last;
    last = curr;
  }
  return last[b.length];
}

/**
 * Splits text into segments marking which parts matched a query term, so the
 * UI can highlight without doing its own (divergent) matching. Offsets refer
 * to the ORIGINAL string, so display text is never altered.
 */
export interface HighlightSegment {
  text: string;
  match: boolean;
}

export function highlight(original: string, terms: string[]): HighlightSegment[] {
  if (!original || terms.length === 0) return [{ text: original, match: false }];
  const normalizedTerms = terms.map(normalizeText).filter(Boolean);
  if (normalizedTerms.length === 0) return [{ text: original, match: false }];

  // Map each character of the original to its normalised position so that we
  // can search on normalised text but slice on the original.
  const ranges: Array<[number, number]> = [];
  const lowerOriginal = normalizeChars(original);
  for (const term of normalizedTerms) {
    let from = 0;
    for (;;) {
      const idx = lowerOriginal.text.indexOf(term, from);
      if (idx === -1) break;
      const start = lowerOriginal.map[idx];
      const endIdx = idx + term.length - 1;
      const end = (lowerOriginal.map[endIdx] ?? original.length - 1) + 1;
      ranges.push([start, end]);
      from = idx + term.length;
    }
  }
  if (ranges.length === 0) return [{ text: original, match: false }];

  ranges.sort((x, y) => x[0] - y[0]);
  const merged: Array<[number, number]> = [];
  for (const r of ranges) {
    const lastRange = merged[merged.length - 1];
    if (lastRange && r[0] <= lastRange[1]) lastRange[1] = Math.max(lastRange[1], r[1]);
    else merged.push([...r] as [number, number]);
  }

  const out: HighlightSegment[] = [];
  let cursor = 0;
  for (const [start, end] of merged) {
    if (start > cursor) out.push({ text: original.slice(cursor, start), match: false });
    out.push({ text: original.slice(start, end), match: true });
    cursor = end;
  }
  if (cursor < original.length) out.push({ text: original.slice(cursor), match: false });
  return out;
}

/**
 * Normalises character-by-character, keeping a map from each output character
 * back to its source index in the original string.
 */
function normalizeChars(original: string): { text: string; map: number[] } {
  let text = '';
  const map: number[] = [];
  let pendingSpace = false;
  // Iterate by code point so that astral characters keep correct offsets.
  for (let i = 0; i < original.length; ) {
    const cp = original.codePointAt(i)!;
    const ch = String.fromCodePoint(cp);
    const width = ch.length;
    const norm = normalizeText(ch);
    if (!norm) {
      if (text.length > 0) pendingSpace = true;
      i += width;
      continue;
    }
    if (pendingSpace) {
      text += ' ';
      map.push(i);
      pendingSpace = false;
    }
    for (const c of norm) {
      text += c;
      map.push(i);
    }
    i += width;
  }
  return { text, map };
}
