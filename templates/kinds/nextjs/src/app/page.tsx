import { NoteForm } from "@/features/notes/components/note-form";
import { NoteList } from "@/features/notes/components/note-list";
import { notesService } from "@/features/notes/service";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const notes = await notesService().list();
  return (
    <div className="grid gap-8">
      <section className="grid gap-4">
        <h1 className="text-2xl font-semibold">Notes</h1>
        <NoteForm />
      </section>
      <NoteList notes={notes} />
    </div>
  );
}
