import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Note } from "../schema";

export function NoteList({ notes }: { notes: Note[] }) {
  if (notes.length === 0) {
    return <p className="text-muted-foreground text-sm">No notes yet. Write the first one.</p>;
  }
  return (
    <ul className="grid gap-3" aria-label="Notes">
      {notes.map((note) => (
        <li key={note.id}>
          <Card>
            <CardHeader>
              <CardTitle>{note.title}</CardTitle>
              <time className="text-muted-foreground text-xs" dateTime={note.createdAt.toISOString()}>
                {note.createdAt.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}
              </time>
            </CardHeader>
            {note.body && <CardContent className="whitespace-pre-wrap text-sm">{note.body}</CardContent>}
          </Card>
        </li>
      ))}
    </ul>
  );
}
