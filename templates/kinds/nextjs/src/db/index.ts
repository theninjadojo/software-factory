import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import { env } from "@/lib/env";
import * as schema from "./schema";

function connect() {
  return drizzle(postgres(env().DATABASE_URL, { max: 10 }), { schema });
}

export type Database = ReturnType<typeof connect>;

// One pool per process, kept across hot reloads in development.
const globalForDb = globalThis as unknown as { db?: Database };

export function db(): Database {
  globalForDb.db ??= connect();
  return globalForDb.db;
}
