import { afterEach, describe, expect, it, vi } from 'vitest';
import { DailyMedProvider } from './dailymed.js';
import { SourceLookupError } from './types.js';

/**
 * A response shaped exactly as DailyMed's /spls.json documents it. The live
 * service is not called from the test suite: it is someone else's public
 * infrastructure, and a test that depends on it fails for reasons that have
 * nothing to do with this code.
 */
const SPL_RESPONSE = {
  metadata: {
    total_elements: 3,
    elements_per_page: 8,
    current_page: 1,
    total_pages: 1,
  },
  data: [
    {
      spl_version: 7,
      published_date: 'Mar 14, 2024',
      setid: 'fdbfe194-b845-42c5-bb87-a48118bc72e7',
      title: 'SERTRALINE HYDROCHLORIDE tablet, film coated [Northstar Rx LLC]',
    },
    {
      spl_version: 3,
      published_date: 'Jan 02, 2023',
      setid: '11112222-3333-4444-5555-666677778888',
      title: 'ZOLOFT- sertraline hydrochloride solution, concentrate [Roerig]',
    },
    { spl_version: 1, published_date: 'Feb 01, 2020', title: 'No setid here' },
  ],
};

function mockFetch(handler: (url: URL) => Response | Promise<Response>) {
  const spy = vi.fn(async (input: string | URL | Request) => {
    const url = new URL(typeof input === 'string' ? input : input.toString());
    return handler(url);
  });
  vi.stubGlobal('fetch', spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe('DailyMed provider', () => {
  it('asks for the medication by name', async () => {
    const spy = mockFetch(() => Response.json(SPL_RESPONSE));
    await new DailyMedProvider().search('Sertraline', {
      signal: AbortSignal.timeout(5000),
      limit: 8,
    });

    const url = new URL(spy.mock.calls[0][0] as string);
    expect(url.pathname).toContain('/services/v2/spls.json');
    expect(url.searchParams.get('drug_name')).toBe('Sertraline');
    expect(Number(url.searchParams.get('pagesize'))).toBe(8);
  });

  it('turns labels into suggestions that point at the readable page', async () => {
    mockFetch(() => Response.json(SPL_RESPONSE));
    const results = await new DailyMedProvider().search('Sertraline', {
      signal: AbortSignal.timeout(5000),
      limit: 8,
    });

    expect(results).toHaveLength(2);
    expect(results[0]).toMatchObject({
      provider: 'dailymed',
      externalId: 'fdbfe194-b845-42c5-bb87-a48118bc72e7',
      title: 'SERTRALINE HYDROCHLORIDE',
      publishedAt: 'Mar 14, 2024',
      jurisdiction: 'US',
    });
    expect(results[0].url).toContain('drugInfo.cfm?setid=fdbfe194-b845-42c5-bb87-a48118bc72e7');
    // Dosage form and labeller are kept, so two labels can be told apart.
    expect(results[0].description).toBe('tablet, film coated — Northstar Rx LLC');
  });

  it('drops a record with no identifier rather than inventing one', async () => {
    mockFetch(() => Response.json(SPL_RESPONSE));
    const results = await new DailyMedProvider().search('Sertraline', {
      signal: AbortSignal.timeout(5000),
      limit: 8,
    });
    expect(results.every((r) => r.externalId)).toBe(true);
    expect(results.map((r) => r.title)).not.toContain('No setid here');
  });

  it('always reports the jurisdiction the document governs', async () => {
    mockFetch(() => Response.json(SPL_RESPONSE));
    const results = await new DailyMedProvider().search('Sertraline', {
      signal: AbortSignal.timeout(5000),
      limit: 8,
    });
    // A US label says nothing about approval elsewhere, so this is never blank.
    expect(results.every((r) => r.jurisdiction === 'US')).toBe(true);
  });

  it('returns nothing for an unknown medication', async () => {
    mockFetch(() => Response.json({ metadata: { total_elements: 0 }, data: [] }));
    const results = await new DailyMedProvider().search('Notadrug', {
      signal: AbortSignal.timeout(5000),
      limit: 8,
    });
    expect(results).toEqual([]);
  });

  it('reports a failure rather than pretending there are no sources', async () => {
    mockFetch(() => new Response('upstream is down', { status: 503 }));
    await expect(
      new DailyMedProvider().search('Sertraline', { signal: AbortSignal.timeout(5000), limit: 8 }),
    ).rejects.toBeInstanceOf(SourceLookupError);
  });

  it('reports malformed output rather than crashing', async () => {
    mockFetch(() => new Response('<html>not json</html>', { status: 200 }));
    await expect(
      new DailyMedProvider().search('Sertraline', { signal: AbortSignal.timeout(5000), limit: 8 }),
    ).rejects.toBeInstanceOf(SourceLookupError);
  });

  it('reports being unable to reach the service', async () => {
    mockFetch(() => {
      throw new TypeError('fetch failed');
    });
    await expect(
      new DailyMedProvider().search('Sertraline', { signal: AbortSignal.timeout(5000), limit: 8 }),
    ).rejects.toBeInstanceOf(SourceLookupError);
  });

  it('honours the limit it is given', async () => {
    mockFetch(() => Response.json(SPL_RESPONSE));
    const results = await new DailyMedProvider().search('Sertraline', {
      signal: AbortSignal.timeout(5000),
      limit: 1,
    });
    expect(results).toHaveLength(1);
  });
});
