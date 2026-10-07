import { describe, expect, it } from "vitest";
import { parseEnv } from "./env";

const base = { DATABASE_URL: "postgres://u:p@localhost:5432/app", BETTER_AUTH_SECRET: "x".repeat(32) };

describe("parseEnv", () => {
  it("accepts a minimal configuration and treats empty values as unset", () => {
    const env = parseEnv({ ...base, GITHUB_CLIENT_ID: "", GITHUB_CLIENT_SECRET: "" });
    expect(env.GITHUB_CLIENT_ID).toBeUndefined();
  });

  it("names every missing or bad variable", () => {
    expect(() => parseEnv({ DATABASE_URL: "mysql://x" })).toThrow(/DATABASE_URL[\s\S]*BETTER_AUTH_SECRET/);
  });

  it("wants both GitHub credentials or neither", () => {
    expect(() => parseEnv({ ...base, GITHUB_CLIENT_ID: "id" })).toThrow(/GITHUB_CLIENT_SECRET/);
  });
});
