// Runs once when the server starts (not during the build): a bad configuration stops the server at once.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { exitOnBadEnv } = await import("./lib/startup");
    exitOnBadEnv();
  }
}
