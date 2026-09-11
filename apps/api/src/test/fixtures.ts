import type { MedicationData } from '@med/shared';
import { provided } from '@med/shared';

/** Builds a medication record with only the fields a test cares about. */
export function medication(fields: Record<string, string>, locale: 'en' | 'he' = 'en'): MedicationData {
  const data: MedicationData = {};
  for (const [key, text] of Object.entries(fields)) {
    data[key] = {
      en: locale === 'en' ? provided(text) : { state: 'not_supplied', text: null },
      he: locale === 'he' ? provided(text) : { state: 'not_supplied', text: null },
    };
  }
  return data;
}

/** A record complete enough to be publishable once citations are attached. */
export const SERTRALINE = medication({
  generic_name: 'Sertraline',
  trade_names: 'Zoloft, Lustral',
  therapeutic_group: 'Antidepressants',
  drug_class: 'SSRI',
  mechanism: 'Selective serotonin reuptake inhibitor',
  formulation: 'Tablet',
  available_strengths: '25, 50, 100 mg',
  dose_range: '50-200 mg/day',
  starting_dose: '50 mg/day',
  maximum_dose: '200 mg/day',
  adult_indications: 'Major depressive disorder, OCD, panic disorder',
  contraindications: 'Concurrent MAOI use',
  qtc_adults: 'Minimal effect at therapeutic doses',
});

export const METHYLPHENIDATE = medication({
  generic_name: 'Methylphenidate',
  trade_names: 'Ritalin IR',
  therapeutic_group: 'ADHD',
  drug_class: 'Stimulants',
  mechanism: 'Dopamine and norepinephrine transporter (DAT&NET) agonist',
  formulation: 'Immediate-release tablet',
  available_strengths: '10 mg',
  dose_range: '5-60 mg/day',
  starting_dose: '5-10 mg/day (morning)',
  maximum_dose: '2mg/kg/day',
  contraindications: 'Hyperthyroidism, High BP, Tachycardia, Concurrent MAOI use, Glaucoma',
});
