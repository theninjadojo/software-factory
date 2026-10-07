import { describe, expect, it } from "vitest";
import { AppError } from "@/lib/errors";
import type { NotesRepository } from "./repository";
import type { NewNote, Note } from "./schema";
import { createNotesService } from "./service";

function fakeRepository(): NotesRepository & { saved: Note[] } {
  const saved: Note[] = [];
  return {
    saved,
    list: async (limit) => [...saved].reverse().slice(0, limit),
    create: async (note: NewNote) => {
      const row = { id: String(saved.length + 1), createdAt: new Date(), ...note };
      saved.push(row);
      return row;
    },
  };
}

describe("notes service", () => {
  it("creates a note with trimmed text and lists newest first", async () => {
    const repo = fakeRepository();
    const service = createNotesService(repo);

    await service.create({ title: "  First  " });
    await service.create({ title: "Second", body: "details" });

    const listed = await service.list();
    expect(listed.map((n) => n.title)).toEqual(["Second", "First"]);
    expect(listed[1].body).toBe("");
  });

  it("refuses a note without a title and says which field is wrong", async () => {
    const service = createNotesService(fakeRepository());

    const error = await service.create({ title: "   " }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AppError);
    expect(error).toMatchObject({ code: "validation", fields: { title: ["Give the note a title"] } });
  });
});
