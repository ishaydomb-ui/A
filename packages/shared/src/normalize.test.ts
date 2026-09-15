import { describe, expect, it } from 'vitest';
import { editDistance, highlight, normalizeText, tokenize } from './normalize.js';

describe('normalizeText', () => {
  it('lower-cases and strips punctuation', () => {
    expect(normalizeText('Sertraline (Zoloft)')).toBe('sertraline zoloft');
  });

  it('folds Latin diacritics', () => {
    expect(normalizeText('Lévomépromazine')).toBe('levomepromazine');
  });

  it('strips Hebrew niqqud', () => {
    expect(normalizeText('סֶרְטְרָלִין')).toBe(normalizeText('סרטרלין'));
  });

  it('folds Hebrew final letters so word position stops mattering', () => {
    expect(normalizeText('ריטלין')).toBe(normalizeText('ריטלינ'));
    expect(normalizeText('דיכאון')).toBe('דיכאונ');
  });

  it('removes geresh and gershayim', () => {
    expect(normalizeText('מ"ג')).toBe('מג');
    expect(normalizeText("ג'")).toBe('ג');
  });

  it('collapses whitespace', () => {
    expect(normalizeText('  10   mg \n per day ')).toBe('10 mg per day');
  });

  it('returns an empty string for punctuation-only input', () => {
    expect(normalizeText('---')).toBe('');
  });
});

describe('tokenize', () => {
  it('splits normalised text into terms', () => {
    expect(tokenize('Ritalin LA 20mg')).toEqual(['ritalin', 'la', '20mg']);
  });
});

describe('editDistance', () => {
  it('is zero for identical strings', () => {
    expect(editDistance('sertraline', 'sertraline')).toBe(0);
  });

  it('counts a single deletion', () => {
    expect(editDistance('sertraline', 'sertrline')).toBe(1);
  });

  it('counts a transposition as one edit', () => {
    expect(editDistance('sertraline', 'sertralnie')).toBe(1);
  });

  it('gives up beyond the cap rather than doing the full work', () => {
    expect(editDistance('sertraline', 'methylphenidate', 3)).toBeGreaterThan(3);
  });
});

describe('highlight', () => {
  it('marks the matched span and keeps the original casing', () => {
    const segments = highlight('Sertraline hydrochloride', ['sertraline']);
    expect(segments[0]).toEqual({ text: 'Sertraline', match: true });
    expect(segments.map((s) => s.text).join('')).toBe('Sertraline hydrochloride');
  });

  it('matches through diacritics without altering the display text', () => {
    const segments = highlight('Lévomépromazine', ['levo']);
    expect(segments[0]).toEqual({ text: 'Lévo', match: true });
    expect(segments.map((s) => s.text).join('')).toBe('Lévomépromazine');
  });

  it('matches Hebrew regardless of final-letter form', () => {
    const segments = highlight('ריטלין', ['ריטלינ']);
    expect(segments.some((s) => s.match)).toBe(true);
    expect(segments.map((s) => s.text).join('')).toBe('ריטלין');
  });

  it('merges overlapping matches', () => {
    const segments = highlight('Zoloft', ['zol', 'olo']);
    expect(segments.filter((s) => s.match)).toHaveLength(1);
    expect(segments.map((s) => s.text).join('')).toBe('Zoloft');
  });

  it('returns the whole string unmatched when nothing matches', () => {
    expect(highlight('Zoloft', ['ritalin'])).toEqual([{ text: 'Zoloft', match: false }]);
  });
});
