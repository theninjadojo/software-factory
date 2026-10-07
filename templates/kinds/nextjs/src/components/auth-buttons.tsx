"use client";

import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";

export function SignInWithGitHub() {
  return (
    <Button size="sm" onClick={() => authClient.signIn.social({ provider: "github", callbackURL: "/" })}>
      Sign in with GitHub
    </Button>
  );
}

export function SignOut() {
  const router = useRouter();
  return (
    <Button
      size="sm"
      variant="outline"
      onClick={() => authClient.signOut({ fetchOptions: { onSuccess: () => router.refresh() } })}
    >
      Sign out
    </Button>
  );
}
