import { createFileRoute } from "@tanstack/react-router";
import { ItemsPage } from "@/features/items/components/items-page";

export const Route = createFileRoute("/")({
  component: ItemsPage,
});
