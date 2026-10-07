// The one error type the app throws on purpose. Services throw it; server actions turn it into an ActionResult;
// anything else is a bug, logged on the server and shown as a generic message.
export type ErrorCode = "validation" | "not_found" | "unauthorized" | "conflict" | "internal";

export class AppError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly fields?: Record<string, string[]>,
  ) {
    super(message);
    this.name = "AppError";
  }

  static validation(message: string, fields?: Record<string, string[]>) {
    return new AppError("validation", message, fields);
  }

  static notFound(message = "Not found") {
    return new AppError("not_found", message);
  }
}

export type ActionError = { code: ErrorCode; message: string; fields?: Record<string, string[]> };
export type ActionResult<T> = { ok: true; data: T } | { ok: false; error: ActionError };

/** Runs a server action's work and returns a serialisable result instead of throwing. */
export async function toActionResult<T>(work: () => Promise<T>): Promise<ActionResult<T>> {
  try {
    return { ok: true, data: await work() };
  } catch (err) {
    if (err instanceof AppError) {
      return { ok: false, error: { code: err.code, message: err.message, fields: err.fields } };
    }
    console.error(err);
    return { ok: false, error: { code: "internal", message: "Something went wrong. Please try again." } };
  }
}
