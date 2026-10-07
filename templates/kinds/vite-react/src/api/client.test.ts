import { describe, expect, it } from "vitest";
import { toApiError } from "./client";

describe("toApiError", () => {
  it("uses FastAPI's message", () => {
    expect(toApiError(new Response(null, { status: 422 }), { detail: "too long" }).message).toBe("too long");
  });

  it("joins validation messages", () => {
    const body = { detail: [{ msg: "String should have at least 1 character" }, { msg: "Field required" }] };
    expect(toApiError(new Response(null, { status: 422 }), body).message).toBe("String should have at least 1 character; Field required");
  });

  it("falls back to the status", () => {
    expect(toApiError(new Response(null, { status: 502 }), undefined).message).toBe("The server answered 502.");
  });
});
