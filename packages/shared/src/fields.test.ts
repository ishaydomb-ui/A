import { describe, expect, it } from 'vitest';
import { CLASS_PROFILES, DRUG_CLASSES, isFieldKey, profileFor, profileLabel } from './fields.js';

describe('per-class profiles', () => {
  it('only reference fields that exist in the registry', () => {
    // A typo here would render a row with a blank heading and no value, which
    // reads as missing clinical data rather than as a bug.
    for (const [className, profile] of Object.entries(CLASS_PROFILES)) {
      for (const entry of profile) {
        expect(isFieldKey(entry.key), `${className} → ${entry.key}`).toBe(true);
      }
    }
  });

  it('do not list the same field twice', () => {
    for (const [className, profile] of Object.entries(CLASS_PROFILES)) {
      const keys = profile.map((e) => e.key);
      expect(new Set(keys).size, `${className} repeats a field`).toBe(keys.length);
    }
  });

  it('lead with the generic name', () => {
    for (const className of DRUG_CLASSES) {
      expect(CLASS_PROFILES[className][0].key).toBe('generic_name');
    }
  });

  it('are found by therapeutic group, ignoring surrounding whitespace', () => {
    expect(profileFor('Antipsychotics')).toBe(CLASS_PROFILES.Antipsychotics);
    expect(profileFor('  ADHD  ')).toBe(CLASS_PROFILES.ADHD);
  });

  it('fall back to null for a group with no profile, so the caller keeps the grouped view', () => {
    expect(profileFor('Anxiolytics')).toBeNull();
    expect(profileFor('')).toBeNull();
    expect(profileFor(null)).toBeNull();
    expect(profileFor(undefined)).toBeNull();
  });

  it('take the sheet heading where one is given, and the registry label otherwise', () => {
    expect(profileLabel({ key: 'dose_range', label: { en: 'Dosage', he: 'מינונים' } }, 'en')).toBe('Dosage');
    expect(profileLabel({ key: 'dose_range' }, 'en')).toBe('Dose range');
    expect(profileLabel({ key: 'dose_range' }, 'he')).toBe('טווח מינון');
  });

  it('omit QTc from classes whose source sheet has no QTc column', () => {
    // The ADHD and mood-stabiliser sheets have no QTc columns at all. Showing
    // the field for them was what made "not supplied" read as missing data.
    for (const className of ['ADHD', 'Mood stabilizers'] as const) {
      const keys = CLASS_PROFILES[className].map((e) => e.key);
      expect(keys).not.toContain('qtc_adults');
      expect(keys).not.toContain('qtc_pediatrics');
    }
  });
});
