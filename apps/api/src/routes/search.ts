import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { LOCALES, MAX_COMPARE, roleHasCapability } from '@med/shared';
import * as limits from '../services/rateLimit.js';
import * as search from '../services/search.js';

/** Accepts either repeated params or a comma-separated list. */
const listParam = z
  .union([z.string(), z.array(z.string())])
  .optional()
  .transform((v) => {
    if (!v) return undefined;
    const raw = Array.isArray(v) ? v : v.split(',');
    const cleaned = raw.map((s) => s.trim()).filter(Boolean);
    return cleaned.length ? cleaned : undefined;
  });

export default async function searchRoutes(app: FastifyInstance): Promise<void> {
  const requireRead = app.requireCapability('catalogue:read_published');

  app.get('/', { onRequest: [requireRead] }, async (req) => {
    const q = z
      .object({
        q: z.string().max(200).default(''),
        locale: z.enum(LOCALES).default('en'),
        therapeuticGroup: listParam,
        drugClass: listParam,
        drugFamily: listParam,
        formulation: listParam,
        limit: z.coerce.number().int().min(1).max(100).default(25),
        offset: z.coerce.number().int().min(0).max(10_000).default(0),
        includeUnpublished: z.coerce.boolean().default(false),
      })
      .parse(req.query);

    await limits.enforce('search', req.currentUser!.id);

    // Unpublished content is only ever searchable by a role that may read it.
    const includeUnpublished =
      q.includeUnpublished && roleHasCapability(req.currentUser!.role, 'catalogue:read_unpublished');

    const result = await search.search({
      q: q.q,
      locale: q.locale,
      filters: {
        therapeuticGroup: q.therapeuticGroup,
        drugClass: q.drugClass,
        drugFamily: q.drugFamily,
        formulation: q.formulation,
      },
      limit: q.limit,
      offset: q.offset,
      includeUnpublished,
    });

    return { ...result, limit: q.limit, offset: q.offset, maxCompare: MAX_COMPARE };
  });

  app.get('/suggest', { onRequest: [requireRead] }, async (req) => {
    const q = z
      .object({
        q: z.string().max(200),
        locale: z.enum(LOCALES).default('en'),
        includeUnpublished: z.coerce.boolean().default(false),
      })
      .parse(req.query);
    await limits.enforce('search', req.currentUser!.id);

    const includeUnpublished =
      q.includeUnpublished && roleHasCapability(req.currentUser!.role, 'catalogue:read_unpublished');
    return { suggestions: await search.suggest(q.q, q.locale, includeUnpublished) };
  });

  app.get('/facets', { onRequest: [requireRead] }, async (req) => {
    const q = z.object({ includeUnpublished: z.coerce.boolean().default(false) }).parse(req.query);
    const includeUnpublished =
      q.includeUnpublished && roleHasCapability(req.currentUser!.role, 'catalogue:read_unpublished');
    return { facets: await search.facets(includeUnpublished) };
  });
}
