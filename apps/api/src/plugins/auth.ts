import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import fp from 'fastify-plugin';
import type { Capability, Role } from '@med/shared';
import { roleHasCapability } from '@med/shared';
import { config } from '../config.js';
import { forbidden, unauthorized } from '../lib/errors.js';
import { resolveSession, type AuthenticatedUser } from '../services/auth.js';

export const SESSION_COOKIE = 'medcat_session';

declare module 'fastify' {
  interface FastifyRequest {
    currentUser?: AuthenticatedUser;
  }
  interface FastifyInstance {
    /** Requires a valid session; throws 401 otherwise. */
    requireAuth(req: FastifyRequest, reply: FastifyReply): Promise<void>;
    /** Requires a valid session that also holds `capability`. */
    requireCapability(capability: Capability): (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
    /** Requires one of the listed roles. Prefer capabilities where possible. */
    requireRole(...roles: Role[]): (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
  }
}

export function setSessionCookie(reply: FastifyReply, token: string, expiresAt: Date): void {
  reply.setCookie(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: config.cookieSecure,
    // 'lax' still blocks cross-site POSTs while keeping links from email working.
    sameSite: 'lax',
    path: '/',
    domain: config.cookieDomain,
    expires: expiresAt,
    signed: false,
  });
}

/**
 * Capability assertion usable outside a route hook, e.g. when one endpoint
 * serves both a public and a privileged view.
 */
export function assertCapability(
  user: AuthenticatedUser | undefined,
  capability: Capability,
): asserts user is AuthenticatedUser {
  if (!user) throw unauthorized();
  if (!roleHasCapability(user.role, capability)) {
    throw forbidden('forbidden', 'Your role does not permit this action.');
  }
}

export function clearSessionCookie(reply: FastifyReply): void {
  reply.clearCookie(SESSION_COOKIE, { path: '/', domain: config.cookieDomain });
}

/**
 * Authentication and authorisation are enforced here, on the server, for
 * every route that declares a requirement. The web client's role checks are
 * presentation only and are never trusted.
 */
export default fp(async function authPlugin(app: FastifyInstance) {
  // Populate req.currentUser for every request, without requiring auth.
  app.addHook('onRequest', async (req) => {
    const token = req.cookies?.[SESSION_COOKIE];
    if (!token) return;
    const user = await resolveSession(token);
    if (user) req.currentUser = user;
  });

  app.decorate('requireAuth', async function requireAuth(req: FastifyRequest) {
    if (!req.currentUser) throw unauthorized();
  });

  app.decorate('requireCapability', function requireCapability(capability: Capability) {
    return async function check(req: FastifyRequest) {
      const user = req.currentUser;
      if (!user) throw unauthorized();
      if (!roleHasCapability(user.role, capability)) {
        throw forbidden('forbidden', 'Your role does not permit this action.');
      }
    };
  });

  app.decorate('requireRole', function requireRole(...roles: Role[]) {
    return async function check(req: FastifyRequest) {
      const user = req.currentUser;
      if (!user) throw unauthorized();
      if (!roles.includes(user.role)) {
        throw forbidden('forbidden', 'Your role does not permit this action.');
      }
    };
  });
});
