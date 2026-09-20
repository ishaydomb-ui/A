import { describe, expect, it } from 'vitest';
import {
  CLASS_PROFILES,
  DRUG_CLASSES,
  isFieldKey,
  profileFor,
  profileLabel,
  resolveProfileField,
} from './fields.js';

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

  it('match a class name whatever its capitalisation', () => {
    // The website snapshot writes "Mood Stabilizers", the authoritative
    // workbook "Mood stabilizers". One class, one layout.
    expect(profileFor('Mood Stabilizers')).toBe(CLASS_PROFILES['Mood stabilizers']);
    expect(profileFor('mood stabilizers')).toBe(CLASS_PROFILES['Mood stabilizers']);
    expect(profileFor('adhd')).toBe(CLASS_PROFILES.ADHD);
  });

  it('reference only real fields in their fallback keys too', () => {
    for (const [className, profile] of Object.entries(CLASS_PROFILES)) {
      for (const entry of profile) {
        for (const key of entry.fallbackKeys ?? []) {
          expect(isFieldKey(key), `${className} → ${entry.key} → ${key}`).toBe(true);
        }
      }
    }
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

  it('fall back to a legacy key when the profile field is empty', () => {
    const entry = { key: 'titration_adults', fallbackKeys: ['titration'] };
    expect(resolveProfileField(entry, (k) => k === 'titration')).toBe('titration');
    expect(resolveProfileField(entry, (k) => k === 'titration_adults')).toBe('titration_adults');
    // Nothing anywhere: report the profile's own field as empty rather than
    // silently hiding the row.
    expect(resolveProfileField(entry, () => false)).toBe('titration_adults');
  });

  it('label a fallback value with the field it actually came from', () => {
    // An older record's trade names are not known to be the Israeli brands,
    // so they must not be headed as though they were.
    const entry = {
      key: 'brand_names_israel',
      label: { en: 'Trade names in Israel', he: 'שמות מסחריים בישראל' },
      fallbackKeys: ['trade_names'],
    };
    expect(profileLabel(entry, 'en', 'brand_names_israel')).toBe('Trade names in Israel');
    expect(profileLabel(entry, 'en', 'trade_names')).toBe('Trade names');
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
