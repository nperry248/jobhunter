/**
 * api/client.js — HTTP client for talking to the FastAPI backend.
 *
 * CONCEPT — Why a dedicated API client module?
 *   Instead of calling `fetch("http://localhost:8000/...")` scattered throughout
 *   your components, we centralize all API calls here. Benefits:
 *   - One place to update the base URL when moving to production
 *   - One place to add auth headers, error handling, retry logic
 *   - Components stay clean: they call `api.getJobs()`, not raw fetch()
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

// ── API functions ─────────────────────────────────────────────────────────────

/** Check if the backend API is running. */
export async function checkHealth() {
  return request("/health");
}

/**
 * Fetch a paginated, filtered list of jobs.
 *
 * @param {object} params
 * @param {string|null} params.status  - Filter by status ("new", "scored", etc.) or null for all
 * @param {number}      params.limit   - Max results per page (default 20)
 * @param {number}      params.offset  - How many results to skip (for pagination)
 *
 * @returns {{ jobs: Job[], total: number, limit: number, offset: number }}
 */
export async function getJobs({ status = null, limit = 20, offset = 0 } = {}) {
  // Build the query string from whichever params are set.
  // URLSearchParams handles encoding special characters automatically.
  const params = new URLSearchParams({ limit, offset });
  if (status) params.set("status", status);

  return request(`/api/v1/jobs?${params}`);
}

/**
 * Update a job's status to "reviewed" or "ignored".
 *
 * @param {string} jobId   - UUID of the job to update
 * @param {string} status  - "reviewed" or "ignored"
 *
 * @returns {Job} The updated job object
 */
export async function updateJobStatus(jobId, status) {
  return request(`/api/v1/jobs/${jobId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}
