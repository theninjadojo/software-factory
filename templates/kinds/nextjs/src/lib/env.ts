import { z } from "zod";

// Empty strings (as left by .env.example) count as unset.
const optional = z.preprocess((v) => (v === "" ? undefined : v), z.string().min(1).optional());

const schema = z
  .object({
    DATABASE_URL: z.string().regex(/^postgres(ql)?:\/\//, "must be a postgres:// URL"),
    BETTER_AUTH_SECRET: z.string().min(32, "must be at least 32 characters"),
    BETTER_AUTH_URL: z.preprocess((v) => (v === "" ? undefined : v), z.url().optional()),
    GITHUB_CLIENT_ID: optional,
    GITHUB_CLIENT_SECRET: optional,
  })
  .refine((e) => !e.GITHUB_CLIENT_ID === !e.GITHUB_CLIENT_SECRET, {
    message: "set both GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET, or neither",
    path: ["GITHUB_CLIENT_ID"],
  });

export type Env = z.infer<typeof schema>;

export function parseEnv(source: Record<string, string | undefined>): Env {
  const result = schema.safeParse(source);
  if (!result.success) {
    const problems = result.error.issues.map((i) => `  ${i.path.join(".")}: ${i.message}`).join("\n");
    throw new Error(`Invalid environment configuration:\n${problems}\nSee .env.example.`);
  }
  return result.data;
}

let cached: Env | undefined;

/** The validated environment. Read lazily, so building and unit tests need no configuration;
 * src/instrumentation.ts calls it when the server starts, so a bad configuration stops the server at once. */
export function env(): Env {
  cached ??= parseEnv(process.env);
  return cached;
}
