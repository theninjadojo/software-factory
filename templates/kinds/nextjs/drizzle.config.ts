import { defineConfig } from "drizzle-kit";

// `npm run db:generate` writes a new SQL migration to drizzle/ from src/db/schema.ts; it needs no database.
export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  out: "./drizzle",
  dbCredentials: { url: process.env.DATABASE_URL ?? "" },
});
