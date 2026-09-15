import cookie from '@fastify/cookie';
import cors from '@fastify/cors';
import helmet from '@fastify/helmet';
import multipart from '@fastify/multipart';
import Fastify, { type FastifyBaseLogger, type FastifyInstance } from 'fastify';
import { ZodError } from 'zod';
import { config } from './config.js';
import { AppError } from './lib/errors.js';
import { logger } from './lib/logger.js';
import authPlugin from './plugins/auth.js';
import authRoutes from './routes/auth.js';
import catalogueRoutes from './routes/catalogue.js';
import importRoutes from './routes/imports.js';
import metaRoutes from './routes/meta.js';
import reviewRoutes from './routes/review.js';
import searchRoutes from './routes/search.js';
import userRoutes from './routes/users.js';

export async function buildApp(): Promise<FastifyInstance> {
  const app = Fastify({
    // pino's concrete Logger type is narrower than Fastify's structural
    // logger interface; they are compatible at runtime.
    loggerInstance: logger as unknown as FastifyBaseLogger,
    trustProxy: config.trustProxy,
    bodyLimit: 1024 * 1024,
  });

  await app.register(helmet, {
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        // The API serves JSON only; the web client sets its own policy.
        scriptSrc: ["'self'"],
        styleSrc: ["'self'", "'unsafe-inline'"],
        imgSrc: ["'self'", 'data:'],
        objectSrc: ["'none'"],
        frameAncestors: ["'none'"],
        baseUri: ["'self'"],
        formAction: ["'self'"],
      },
    },
    hsts: config.isProduction ? { maxAge: 31_536_000, includeSubDomains: true } : false,
    crossOriginResourcePolicy: { policy: 'same-site' },
    referrerPolicy: { policy: 'no-referrer' },
  });

  await app.register(cors, {
    origin: config.isProduction ? [config.publicUrl] : true,
    credentials: true,
    methods: ['GET', 'POST', 'PATCH', 'PUT', 'DELETE'],
  });

  await app.register(cookie, {});
  await app.register(multipart, {
    limits: { fileSize: config.maxUploadBytes, files: 1, fields: 20 },
  });
  await app.register(authPlugin);

  app.setErrorHandler((error, req, reply) => {
    if (error instanceof AppError) {
      if (error.statusCode >= 500) req.log.error({ err: error }, 'application error');
      return reply
        .code(error.statusCode)
        .send({ error: { code: error.code, message: error.message, details: error.details } });
    }
    if (error instanceof ZodError) {
      return reply.code(400).send({
        error: {
          code: 'validation_failed',
          message: 'The request contains invalid values.',
          details: error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
        },
      });
    }
    const err = error as { statusCode?: number; message?: string };
    const status = err.statusCode ?? 500;
    if (status === 413) {
      return reply.code(413).send({
        error: { code: 'file_too_large', message: 'The uploaded file is too large.' },
      });
    }
    if (status < 500) {
      return reply
        .code(status)
        .send({ error: { code: 'bad_request', message: err.message ?? 'Bad request' } });
    }
    req.log.error({ err: error }, 'unhandled error');
    return reply.code(500).send({
      error: { code: 'internal_error', message: 'Something went wrong. Please try again.' },
    });
  });

  app.setNotFoundHandler((_req, reply) => {
    reply.code(404).send({ error: { code: 'not_found', message: 'Not found' } });
  });

  await app.register(metaRoutes);
  await app.register(authRoutes, { prefix: '/api/auth' });
  await app.register(userRoutes, { prefix: '/api/users' });
  await app.register(searchRoutes, { prefix: '/api/search' });
  await app.register(catalogueRoutes, { prefix: '/api/medications' });
  await app.register(importRoutes, { prefix: '/api/imports' });
  await app.register(reviewRoutes, { prefix: '/api/review' });

  return app;
}
