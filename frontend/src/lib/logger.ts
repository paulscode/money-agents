/**
 * Production-safe logging utilities.
 *
 * GAP-8: All `console.error()` calls in production builds can leak URL paths,
 * error codes, and component names to the browser console. This module gates
 * error logging behind the Vite `import.meta.env.DEV` flag so they are
 * stripped from production builds.
 *
 * Usage:
 *   import { logError } from '@/lib/logger';
 *   logError('Failed to fetch:', error);
 */

const isDev = import.meta.env.DEV;

/**
 * Log an error message in development only.
 * In production builds, this is a no-op to prevent information leakage.
 */
export function logError(...args: unknown[]): void {
  if (isDev) {
    console.error(...args);
  }
}

/**
 * Log a warning message in development only.
 */
export function logWarn(...args: unknown[]): void {
  if (isDev) {
    console.warn(...args);
  }
}
