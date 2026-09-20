/**
 * Application errors carry an HTTP status and a stable machine-readable code.
 * Messages are safe to show a user and never leak internal detail.
 */
export class AppError extends Error {
  constructor(
    readonly statusCode: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'AppError';
  }
}

export const badRequest = (code: string, message: string, details?: unknown) =>
  new AppError(400, code, message, details);
export const unauthorized = (code = 'unauthorized', message = 'Authentication required') =>
  new AppError(401, code, message);
export const forbidden = (code = 'forbidden', message = 'You do not have permission to do that') =>
  new AppError(403, code, message);
export const notFound = (code = 'not_found', message = 'Not found') =>
  new AppError(404, code, message);
export const conflict = (code: string, message: string, details?: unknown) =>
  new AppError(409, code, message, details);
export const tooManyRequests = (message = 'Too many requests', details?: unknown) =>
  new AppError(429, 'rate_limited', message, details);
