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
 * Update a job's status to "reviewed", "ignored", or "scored" (undo).
 *
 * @param {string} jobId   - UUID of the job to update
 * @param {string} status  - "reviewed", "ignored", or "scored"
 *
 * @returns {Job} The updated job object
 */
export async function updateJobStatus(jobId, status) {
  return request(`/api/v1/jobs/${jobId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

/**
 * Hard-delete all jobs from the database.
 * @returns {void}
 */
export async function clearAllJobs() {
  const response = await fetch(`${BASE_URL}/api/v1/jobs`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText} on DELETE /api/v1/jobs`);
  }
}

// ── Applications API ──────────────────────────────────────────────────────────

/**
 * Fetch a paginated list of applications with embedded job details.
 *
 * @param {object} params
 * @param {string|null} params.tracking_status - Filter by tracking status or null for all
 * @param {number}      params.limit           - Max results per page (default 20)
 * @param {number}      params.offset          - Pagination offset
 *
 * @returns {{ applications: Application[], total: number, limit: number, offset: number }}
 */
export async function getApplications({ tracking_status = null, limit = 20, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit, offset });
  if (tracking_status) params.set("tracking_status", tracking_status);
  return request(`/api/v1/applications?${params}`);
}

/**
 * Update an application's tracking_status (where the user is in the hiring process).
 *
 * @param {string} applicationId    - UUID of the application to update
 * @param {string} tracking_status  - One of: applied, phone_screen, technical_interview,
 *                                    final_round, offer, rejected, ghosted, withdrawn
 *
 * @returns {Application} The updated application object
 */
export async function updateApplicationTracking(applicationId, tracking_status) {
  return request(`/api/v1/applications/${applicationId}`, {
    method: "PATCH",
    body: JSON.stringify({ tracking_status }),
  });
}

/**
 * Hard-delete an application record.
 * @param {string} applicationId - UUID of the application to remove
 * @returns {void}
 */
export async function deleteApplication(applicationId) {
  const response = await fetch(`${BASE_URL}/api/v1/applications/${applicationId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
}

// ── Pipeline API ──────────────────────────────────────────────────────────────

/**
 * Trigger the scrape + score pipeline in the background.
 * Returns immediately — poll getPipelineStatus() to track progress.
 * @returns {{ status: "started" | "already_running" }}
 */
export async function runPipeline() {
  return request("/api/v1/pipeline/run", { method: "POST" });
}

/**
 * Get the current pipeline run state.
 * @returns {{ running: boolean, started_at: string|null, finished_at: string|null, last_result: object|null, last_error: string|null }}
 */
export async function getPipelineStatus() {
  return request("/api/v1/pipeline/status");
}

// ── Profile API ───────────────────────────────────────────────────────────────

/**
 * Fetch the user's profile. Auto-creates an empty one on first call.
 * @returns {Profile}
 */
export async function getProfile() {
  return request("/api/v1/profile");
}

/**
 * Save all profile fields.
 * @param {object} profile - Full profile object to save
 * @returns {Profile} The saved profile
 */
export async function updateProfile(profile) {
  return request("/api/v1/profile", {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

// ── Orchestrator API ───────────────────────────────────────────────────────────

/**
 * Start a new Orchestrator session.
 * Returns immediately — poll getOrchestratorStatus() to track progress.
 *
 * @param {string}  goal    - Natural-language goal (e.g. "Find me 5 good SWE jobs")
 * @param {boolean} dry_run - If true, tools won't fire real agents
 * @returns {{ session_id: string, status: "started" }}
 */
export async function startOrchestrator(goal, dry_run = false, mode = "fresh_scan", handoff = false, max_apply = 5) {
  return request("/api/v1/orchestrator/run", {
    method: "POST",
    body: JSON.stringify({ goal, dry_run, mode, handoff, max_apply }),
  });
}

/**
 * Get the current state of an Orchestrator session.
 * Poll this every 2s while status === "running".
 *
 * @param {string} session_id - UUID returned by startOrchestrator()
 * @returns {SessionStatus}
 */
export async function getOrchestratorStatus(session_id) {
  return request(`/api/v1/orchestrator/status/${session_id}`);
}

/**
 * Approve the pending job list and trigger the Apply Agent.
 * Only valid when the session is in "waiting_for_approval" state.
 *
 * @param {string}        session_id          - The session to approve
 * @param {string[]|null} approved_job_ids    - Specific job IDs to approve, or null for all
 * @returns {{ session_id: string, status: "started" }}
 */
export async function approveOrchestrator(session_id, approved_job_ids = null) {
  return request(`/api/v1/orchestrator/approve/${session_id}`, {
    method: "POST",
    body: JSON.stringify({ approved_job_ids }),
  });
}

/**
 * Fetch past Orchestrator sessions (most recent first).
 *
 * @param {object} params
 * @param {number} params.limit  - Max results (default 20)
 * @param {number} params.offset - Pagination offset
 * @returns {HistoryItem[]}
 */
export async function getOrchestratorHistory({ limit = 20, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit, offset });
  return request(`/api/v1/orchestrator/history?${params}`);
}
