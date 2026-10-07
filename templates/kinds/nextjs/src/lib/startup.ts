import { env } from "./env";

export function exitOnBadEnv() {
  try {
    env();
  } catch (err) {
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  }
}
