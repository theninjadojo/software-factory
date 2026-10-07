"use server";

import { revalidatePath } from "next/cache";
import { toActionResult, type ActionResult } from "@/lib/errors";
import type { Note } from "./schema";
import { notesService } from "./service";

export async function createNote(_previous: ActionResult<Note> | null, form: FormData): Promise<ActionResult<Note>> {
  return toActionResult(async () => {
    const note = await notesService().create({ title: form.get("title") ?? "", body: form.get("body") ?? "" });
    revalidatePath("/");
    return note;
  });
}
