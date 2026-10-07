import createClient from 'openapi-fetch';

import type { components, paths } from './schema';

/** The FastAPI backend. EXPO_PUBLIC_ variables are inlined at build time (see .env.example and DEPLOY.md). */
export const apiUrl = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

/** Typed client generated from the API's OpenAPI schema: `npm run api:types` regenerates ./schema.ts. */
export const api = createClient<paths>({ baseUrl: apiUrl });

export type Schemas = components['schemas'];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(`API request failed with status ${status}`);
    this.name = 'ApiError';
  }
}

/** Unwrap an openapi-fetch result: the data, or an ApiError that TanStack Query can retry or show. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    throw new ApiError(result.response.status, result.error);
  }
  return result.data;
}
