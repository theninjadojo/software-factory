import { toNextJsHandler } from "better-auth/next-js";
import { auth } from "@/lib/auth";

export const GET = (request: Request) => toNextJsHandler(auth()).GET(request);
export const POST = (request: Request) => toNextJsHandler(auth()).POST(request);
