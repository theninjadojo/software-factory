import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { ItemsPage } from "./items-page";

// The generated client is replaced: these tests never touch the network.
vi.mock(import("@/api/client"), async (importOriginal) => ({
  ...(await importOriginal()),
  api: { GET: vi.fn(), POST: vi.fn() } as never,
}));

const GET = vi.mocked(api.GET);
const POST = vi.mocked(api.POST);
const ok = (data: unknown, status = 200) => ({ data, error: undefined, response: new Response(null, { status }) }) as never;
const item = (id: number, name: string) => ({ id, name, created_at: "2026-01-02T10:00:00Z", processed_at: null });

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ItemsPage />
    </QueryClientProvider>,
  );
}

describe("ItemsPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("lists the items from the API", async () => {
    GET.mockResolvedValue(ok([item(1, "First"), item(2, "Second")]));
    renderPage();
    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
    expect(GET).toHaveBeenCalledWith("/items");
  });

  it("adds an item and refreshes the list", async () => {
    GET.mockResolvedValueOnce(ok([])).mockResolvedValue(ok([item(1, "New one")]));
    POST.mockResolvedValue(ok(item(1, "New one"), 201));
    renderPage();
    await screen.findByText(/No items yet/);

    await userEvent.type(screen.getByLabelText("Name"), "  New one ");
    await userEvent.click(screen.getByRole("button", { name: "Add item" }));

    expect(POST).toHaveBeenCalledWith("/items", { body: { name: "New one" } });
    expect(await screen.findByText("New one")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("");
  });

  it("shows the API's error", async () => {
    GET.mockResolvedValue({ data: undefined, error: { detail: "database unavailable" }, response: new Response(null, { status: 503 }) } as never);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("database unavailable");
  });
});
