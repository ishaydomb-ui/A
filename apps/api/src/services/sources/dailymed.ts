import { config } from '../../config.js';
import { SourceLookupError, type SourceProvider, type SourceSuggestion } from './types.js';

/**
 * DailyMed — the US National Library of Medicine's index of approved product
 * labels (Structured Product Labels).
 *
 * A label is a real, citable primary document: it is where a maximum dose or a
 * contraindication is actually written down. That is what the catalogue needs
 * a citation to point at.
 *
 * It governs the United States. It is evidence about a medicine, not evidence
 * about what is approved in another country, and every suggestion says so.
 */
const DEFAULT_BASE = 'https://dailymed.nlm.nih.gov/dailymed';

interface SplRecord {
  setid?: string;
  title?: string;
  published_date?: string;
  spl_version?: number;
}

interface SplResponse {
  metadata?: { total_elements?: number };
  data?: SplRecord[];
}

/**
 * Label titles read like "SERTRALINE HYDROCHLORIDE tablet, film coated [Acme]".
 * Splitting off the dosage form and the labeller keeps the suggestion list
 * readable without discarding what distinguishes one label from another.
 */
function describeLabel(title: string): { name: string; description: string | null } {
  const labeller = /\[([^\]]+)\]\s*$/.exec(title);
  const withoutLabeller = title.replace(/\s*\[[^\]]+\]\s*$/, '').trim();
  const parts = withoutLabeller.split(/\s+(?=(?:tablet|capsule|solution|injection|suspension|film|powder|patch|spray|cream|gel|syrup)\b)/i);

  const name = (parts[0] ?? withoutLabeller).trim();
  const form = parts.slice(1).join(' ').trim();
  const bits = [form || null, labeller ? labeller[1].trim() : null].filter(Boolean);
  return { name, description: bits.length ? bits.join(' — ') : null };
}

export class DailyMedProvider implements SourceProvider {
  readonly name = 'dailymed';
  readonly label = 'DailyMed (US FDA labels)';
  readonly describes = 'Approved US product labels: dosing, contraindications, warnings.';
  readonly jurisdiction = 'US';

  constructor(private readonly baseUrl: string = config.sources.dailymedBaseUrl || DEFAULT_BASE) {}

  async search(
    genericName: string,
    options: { signal: AbortSignal; limit: number },
  ): Promise<SourceSuggestion[]> {
    const url = new URL(`${this.baseUrl}/services/v2/spls.json`);
    url.searchParams.set('drug_name', genericName);
    url.searchParams.set('pagesize', String(Math.min(options.limit, 25)));
    url.searchParams.set('page', '1');

    let response: Response;
    try {
      response = await fetch(url, {
        signal: options.signal,
        headers: { accept: 'application/json', 'user-agent': 'MedicationCatalogue/1.0' },
      });
    } catch (err) {
      if ((err as Error).name === 'AbortError') throw err;
      throw new SourceLookupError(this.name, 'Could not reach DailyMed.', err);
    }

    if (!response.ok) {
      throw new SourceLookupError(this.name, `DailyMed returned ${response.status}.`);
    }

    let body: SplResponse;
    try {
      body = (await response.json()) as SplResponse;
    } catch (err) {
      throw new SourceLookupError(this.name, 'DailyMed returned something that is not JSON.', err);
    }

    const records = Array.isArray(body.data) ? body.data : [];
    return records
      .filter((record): record is SplRecord & { setid: string } => typeof record.setid === 'string')
      .slice(0, options.limit)
      .map((record) => {
        const title = record.title?.trim() || genericName;
        const { name, description } = describeLabel(title);
        return {
          provider: this.name,
          externalId: record.setid,
          title: name,
          // The human-readable label page, which is what a reviewer should
          // read before accepting the citation.
          url: `${this.baseUrl}/drugInfo.cfm?setid=${encodeURIComponent(record.setid)}`,
          description,
          publishedAt: record.published_date?.trim() || null,
          jurisdiction: this.jurisdiction,
        } satisfies SourceSuggestion;
      });
  }
}
