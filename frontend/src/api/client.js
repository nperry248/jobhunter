/**
 * api/client.js — HTTP client for talking to the FastAPI backend.
 *
 * CONCEPT — Why a dedicated API client module?
 *   Instead of calling `fetch("http://localhost:8000/...")` scattered throughout
 *   your components, we centralize all API calls here. Benefits:
 *   - One place to update the base URL when moving to production
 *   - One place to add auth headers, error handling, retry logic
 *   - Components stay clean: they call `api.getJobs()`, not raw fetch()
 *
 * This is a stub — real API functions will be added in Session 2 when
 * the Jobs endpoint is implemented.
 */

// Base URL of the FastAPI backend.
// In production, this would be an environment variable (VITE_API_URL).
// Vite exposes env vars prefixed with VITE_ to the browser bundle.
const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

/**
 * Generic fetch wrapper with error handling.
 * All API functions below use this so error handling is consistent.
 */
async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    // Throw a descriptive error — React components can catch this in error boundaries
    throw new Error(`API error: ${response.status} ${response.statusText} on ${path}`);
  }

  return response.json();
}

// ── API functions (add more as routes are implemented) ────────────────────────

/** Check if the backend API is running. */
export async function checkHealth() {
  return request("/health");
}

// TODO (Session 2): Add getJobs(), updateJobStatus(), etc.
// TODO (Session 2): Add getUserProfile(), updateUserProfile()
// TODO (Session 3): Add getApplications(), triggerApply()
