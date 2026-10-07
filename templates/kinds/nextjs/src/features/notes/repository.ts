import { desc } from "drizzle-orm";
import { db, type Database } from "@/db";
import { notes } from "@/db/schema";
import type { NewNote, Note } from "./schema";

// The notes feature's queries. The service depends on this interface, so unit tests can pass a fake.
export interface NotesRepository {
  list(limit: number): Promise<Note[]>;
  create(note: NewNote): Promise<Note>;
}

export function drizzleNotesRepository(database: Database = db()): NotesRepository {
  return {
    list: (limit) => database.select().from(notes).orderBy(desc(notes.createdAt)).limit(limit),
    async create(note) {
      const [row] = await database.insert(notes).values(note).returning();
      return row;
    },
  };
}
