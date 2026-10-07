import { betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { nextCookies } from "better-auth/next-js";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { db } from "@/db";
import * as schema from "@/db/schema";
import { env } from "@/lib/env";

function createAuth() {
  const { BETTER_AUTH_SECRET, BETTER_AUTH_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET } = env();
  return betterAuth({
    secret: BETTER_AUTH_SECRET,
    baseURL: BETTER_AUTH_URL,
    database: drizzleAdapter(db(), { provider: "pg", schema }),
    socialProviders:
      GITHUB_CLIENT_ID && GITHUB_CLIENT_SECRET
        ? { github: { clientId: GITHUB_CLIENT_ID, clientSecret: GITHUB_CLIENT_SECRET } }
        : {},
    plugins: [nextCookies()],
  });
}

let instance: ReturnType<typeof createAuth> | undefined;

/** Better Auth, created on first use so the build needs no configuration. */
export function auth() {
  instance ??= createAuth();
  return instance;
}

export function githubSignInEnabled() {
  return Boolean(env().GITHUB_CLIENT_ID && env().GITHUB_CLIENT_SECRET);
}

export async function currentUser() {
  const requestHeaders = await headers(); // first: it marks the page dynamic, so the build never needs auth
  const session = await auth().api.getSession({ headers: requestHeaders });
  return session?.user ?? null;
}

/** For pages and actions that need a signed-in user: sends everyone else to the home page. */
export async function requireUser() {
  const user = await currentUser();
  if (!user) redirect("/");
  return user;
}
