import Link from "next/link";
import { currentUser, githubSignInEnabled } from "@/lib/auth";
import { SignInWithGitHub, SignOut } from "./auth-buttons";

export async function SiteHeader() {
  const user = await currentUser();
  return (
    <header className="border-b">
      <div className="mx-auto flex max-w-2xl items-center justify-between gap-4 px-4 py-3">
        <Link href="/" className="font-semibold">
          Shikumi App
        </Link>
        <nav className="flex items-center gap-3 text-sm">
          {user ? (
            <>
              <Link href="/account">{user.name}</Link>
              <SignOut />
            </>
          ) : githubSignInEnabled() ? (
            <SignInWithGitHub />
          ) : null}
        </nav>
      </div>
    </header>
  );
}
