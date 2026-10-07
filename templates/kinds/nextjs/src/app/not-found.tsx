import Link from "next/link";

export default function NotFound() {
  return (
    <div className="grid gap-2">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <Link href="/" className="underline">
        Back to the notes
      </Link>
    </div>
  );
}
