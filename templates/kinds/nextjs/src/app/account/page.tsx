import { requireUser } from "@/lib/auth";

export default async function AccountPage() {
  const user = await requireUser();
  return (
    <div className="grid gap-2">
      <h1 className="text-2xl font-semibold">Your account</h1>
      <p>{user.name}</p>
      <p className="text-muted-foreground text-sm">{user.email}</p>
    </div>
  );
}
