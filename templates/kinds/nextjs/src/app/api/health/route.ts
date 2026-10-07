import { sql } from "drizzle-orm";
import { db } from "@/db";

export const dynamic = "force-dynamic";

// For load balancers and uptime checks: 200 when the app can reach its database.
export async function GET() {
  try {
    await db().execute(sql`select 1`);
    return Response.json({ status: "ok" });
  } catch (err) {
    console.error("health check failed", err);
    return Response.json({ status: "unavailable" }, { status: 503 });
  }
}
