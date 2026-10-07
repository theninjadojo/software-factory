import { z } from "zod";

export const newNoteSchema = z.object({
  title: z.string().trim().min(1, "Give the note a title").max(120, "Keep the title under 120 characters"),
  body: z.string().trim().max(2000, "Keep the note under 2000 characters").default(""),
});

export type NewNote = z.infer<typeof newNoteSchema>;

export type Note = { id: string; title: string; body: string; createdAt: Date };
