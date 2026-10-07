import { z } from "zod";
import { AppError } from "@/lib/errors";
import { drizzleNotesRepository, type NotesRepository } from "./repository";
import { newNoteSchema, type Note } from "./schema";

export const MAX_LISTED = 50;

// Business rules for notes. Server actions and pages call this, never the repository or the database.
export function createNotesService(repo: NotesRepository) {
  return {
    list(): Promise<Note[]> {
      return repo.list(MAX_LISTED);
    },

    async create(input: unknown): Promise<Note> {
      const parsed = newNoteSchema.safeParse(input);
      if (!parsed.success) {
        throw AppError.validation("Check the highlighted fields.", z.flattenError(parsed.error).fieldErrors);
      }
      return repo.create(parsed.data);
    },
  };
}

export const notesService = () => createNotesService(drizzleNotesRepository());
