// Build-time configuration: Vite inlines VITE_ variables when it builds, so set them in the build environment.
export const config = {
  apiUrl: (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, ""),
} as const;
