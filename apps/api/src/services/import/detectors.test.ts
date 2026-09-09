import { describe, expect, it } from 'vitest';
import { runDetectors, type RowContext } from './detectors.js';
import { medication } from '../../test/fixtures.js';

function row(rowNumber: number, name: string, fields: Record<string, string>): RowContext {
  return { rowNumber, name, data: medication({ generic_name: name, ...fields }), raw: {} };
}

const detectorsFor = (ctx: RowContext[], detector: string) =>
  runDetectors(ctx).filter((f) => f.detector === detector);

describe('incomplete threshold', () => {
  it('flags a threshold whose quantity is missing', () => {
    const findings = detectorsFor(
      [row(2, 'Methylphenidate ER', {
        clinical_notes: 'In Israel below the age of 6 and above requires 29 gimel',
      })],
      'incomplete_threshold',
    );
    expect(findings).toHaveLength(1);
    expect(findings[0].severity).toBe('high');
    expect(findings[0].evidence).toContain('above requires');
  });

  it('does not flag the same sentence when the quantity is present', () => {
    const findings = detectorsFor(
      [row(1, 'Methylphenidate', {
        clinical_notes: 'In Israel below the age of 6 and above 90 mg requires 29 gimel',
      })],
      'incomplete_threshold',
    );
    expect(findings).toHaveLength(0);
  });

  it('accepts a threshold separated from its number by connective words', () => {
    const findings = detectorsFor(
      [row(3, 'Drug', { clinical_notes: 'Not licensed below the age of 12 years.' })],
      'incomplete_threshold',
    );
    expect(findings).toHaveLength(0);
  });
});

describe('label inversion', () => {
  it('flags a class abbreviation sitting in the generic name field', () => {
    const findings = detectorsFor(
      [row(11, 'NRI', { generic_name: 'NRI', trade_names: 'Atomoxetine' })],
      'suspected_label_inversion',
    );
    expect(findings).toHaveLength(1);
    expect(findings[0].recommendedAction).toMatch(/do not auto-correct/i);
  });

  it('leaves a normal generic name alone', () => {
    expect(
      detectorsFor([row(1, 'Sertraline', { trade_names: 'Zoloft' })], 'suspected_label_inversion'),
    ).toHaveLength(0);
  });
});

describe('ambiguous side effects', () => {
  it('flags a bare physiological parameter', () => {
    const findings = detectorsFor(
      [row(1, 'Drug', { side_effects: 'Appetite loss, Blood pressure, Tics' })],
      'ambiguous_side_effect',
    );
    expect(findings).toHaveLength(1);
    expect(findings[0].evidence).toContain('Blood pressure');
  });

  it('does not flag a directional description', () => {
    expect(
      detectorsFor(
        [row(1, 'Drug', { side_effects: 'Raised blood pressure, Tics' })],
        'ambiguous_side_effect',
      ),
    ).toHaveLength(0);
  });
});

describe('duplicates and taxonomy', () => {
  it('flags two rows naming the same medication', () => {
    const findings = detectorsFor(
      [row(3, 'Sertraline', {}), row(9, 'Sertraline', {})],
      'duplicate_rows',
    );
    expect(findings).toHaveLength(1);
    expect(findings[0].evidence).toContain('3');
    expect(findings[0].evidence).toContain('9');
  });

  it('flags a label used as both a therapeutic group and a drug class', () => {
    const findings = detectorsFor(
      [
        row(1, 'A', { therapeutic_group: 'Non-Stimulants', drug_class: 'NRI' }),
        row(2, 'B', { therapeutic_group: 'ADHD', drug_class: 'Non-Stimulants' }),
      ],
      'mixed_taxonomy',
    );
    expect(findings).toHaveLength(1);
  });
});

describe('detectors never modify data', () => {
  it('leaves the row content untouched', () => {
    const ctx = row(1, 'NRI', {
      maximum_dose: '2mg/kg/day',
      side_effects: 'Blood pressure',
      clinical_notes: 'above requires 29 gimel',
    });
    const before = JSON.stringify(ctx.data);
    const findings = runDetectors([ctx]);
    expect(findings.length).toBeGreaterThan(0);
    expect(JSON.stringify(ctx.data)).toBe(before);
  });
});
