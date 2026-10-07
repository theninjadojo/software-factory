import createClient from "openapi-fetch";
import { config } from "@/lib/config";
import type { components, paths } from "./schema";

// schema.d.ts is generated from the API's OpenAPI document: `npm run api:types` (needs the API running). Never edit it by hand.
export const api = createClient<paths>({ baseUrl: config.apiUrl });

export type Schemas = components["schemas"];

/** An error answer from the API, with a message fit to show. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// FastAPI answers {"detail": "message"} for its own errors and {"detail": [{"msg": ...}, ...]} for validation errors.
export function toApiError(response: Response, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  let message = `The server answered ${response.status}.`;
  if (typeof detail === "string") message = detail;
  else if (Array.isArray(detail)) message = detail.map((d: { msg?: string }) => d.msg ?? "").filter(Boolean).join("; ") || message;
  return new ApiError(response.status, message);
}
