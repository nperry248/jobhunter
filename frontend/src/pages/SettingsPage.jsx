/**
 * SettingsPage.jsx — User profile and job preferences.
 *
 * WHAT THIS PAGE DOES:
 *   1. Loads the user's profile from GET /api/v1/profile on mount
 *   2. Lets the user edit personal info, links, and job preferences
 *   3. Saves everything via PUT /api/v1/profile on submit
 *
 * WHY THIS EXISTS:
 *   The Apply Agent needs your name, email, LinkedIn, GitHub etc. to fill out
 *   application forms. This page is how you get that info into the system.
 *
 * REACT CONCEPTS:
 *   - Controlled inputs: each input's value is tied to React state, so the
 *     UI always reflects the current state (no stale DOM values)
 *   - Single form object in state: instead of one useState per field, we
 *     use one `form` object and update it with spread syntax. This scales
 *     cleanly to many fields without dozens of state variables.
 */

import { useState, useEffect } from "react";
import { getProfile, updateProfile } from "../api/client";


// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, description, children }) {
  return (
    <div
      className="rounded-lg p-6"
      style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-elevated)' }}
    >
      <div className="mb-5">
        {/* Section title uses monospace label style — consistent with the rest of the UI */}
        <p className="font-mono text-[10px] tracking-widest uppercase mb-1" style={{ color: '#334155' }}>
          {title}
        </p>
        {description && (
          <p className="text-xs" style={{ color: '#475569' }}>{description}</p>
        )}
      </div>
      <div className="space-y-4">
        {children}
      </div>
    </div>
  );
}


// ── Field label wrapper ───────────────────────────────────────────────────────

function Field({ label, hint, children }) {
  return (
    <div>
      <label className="block text-xs font-medium mb-1.5" style={{ color: '#64748b' }}>
        {label}
        {hint && (
          <span className="font-mono text-[10px] tracking-wider ml-1.5" style={{ color: '#334155' }}>
            — {hint}
          </span>
        )}
      </label>
      {children}
    </div>
  );
}

// ── Text input ─────────────────────────────────────────────────────────────────

function Input({ value, onChange, placeholder, type = "text" }) {
  return (
    <input
      type={type}
      value={value ?? ""}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full rounded-md px-3 py-2.5 text-sm focus:outline-none transition-colors"
      style={{
        backgroundColor: 'var(--bg-input)',
        border: '1px solid var(--border)',
        color: 'var(--text-1)',
      }}
      onFocus={e => e.currentTarget.style.borderColor = 'var(--accent-border)'}
      onBlur={e => e.currentTarget.style.borderColor = 'var(--border)'}
    />
  );
}

// ── Toggle switch ─────────────────────────────────────────────────────────────

function Toggle({ label, description, checked, onChange }) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm" style={{ color: 'var(--text-1)' }}>{label}</p>
        {description && (
          <p className="text-xs mt-0.5" style={{ color: '#475569' }}>{description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className="relative w-11 h-6 rounded-full transition-colors shrink-0 ml-4"
        style={{ backgroundColor: checked ? '#a78bfa' : 'rgba(255,255,255,0.08)' }}
      >
        {/* Knob: left-0.5 anchors it 2px from the left edge when off.
            translate-x-5 (20px) shifts it right when on.
            Container is 44px, knob is 20px: 2px gap on both sides. */}
        <span
          className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform shadow-sm"
          style={{ transform: checked ? 'translateX(20px)' : 'translateX(0)' }}
        />
      </button>
    </div>
  );
}


// ── Main Page ─────────────────────────────────────────────────────────────────

export function SettingsPage() {
  // ── State ──────────────────────────────────────────────────────────────────
  // One object for all form fields. We update it with spread syntax:
  //   setForm(prev => ({ ...prev, email: "new@email.com" }))
  // This copies all existing fields and overwrites just the one that changed.
  const [form, setForm]       = useState(null);  // null = not loaded yet
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [saved, setSaved]     = useState(false);
  const [error, setError]     = useState(null);

  // Helper: returns a setter for a single field, leaving all others unchanged.
  const set = (field) => (value) => setForm(prev => ({ ...prev, [field]: value }));

  // ── Load profile on mount ──────────────────────────────────────────────────
  useEffect(() => {
    async function load() {
      try {
        const profile = await getProfile();
        setForm(profile);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  // ── Save ───────────────────────────────────────────────────────────────────
  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const updated = await updateProfile(form);
      setForm(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  // ── Loading skeleton ───────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="p-8 max-w-2xl">
        <div className="mb-8">
          <p className="font-mono text-[10px] tracking-[0.2em] uppercase mb-1" style={{ color: '#334155' }}>
            Configuration
          </p>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--text-1)' }}>Settings</h1>
        </div>
        <div className="space-y-4">
          {[1, 2, 3].map(i => (
            <div
              key={i}
              className="rounded-lg p-6 animate-pulse"
              style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-elevated)' }}
            >
              <div className="h-3 rounded w-1/4 mb-5" style={{ backgroundColor: 'rgba(255,255,255,0.04)' }} />
              <div className="space-y-3">
                <div className="h-9 rounded" style={{ backgroundColor: 'rgba(255,255,255,0.04)' }} />
                <div className="h-9 rounded" style={{ backgroundColor: 'rgba(255,255,255,0.04)' }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error && !form) {
    return (
      <div className="p-8 max-w-2xl">
        <div
          className="rounded-lg p-4"
          style={{ border: '1px solid rgba(248,113,113,0.2)', backgroundColor: 'rgba(248,113,113,0.05)' }}
        >
          <p className="font-semibold text-sm mb-1" style={{ color: '#f87171' }}>Failed to load profile</p>
          <p className="font-mono text-xs mb-2" style={{ color: '#f87171', opacity: 0.7 }}>{error}</p>
          <p className="font-mono text-[10px]" style={{ color: '#475569' }}>
            Is the backend running?{" "}
            <code style={{ color: '#64748b' }}>uvicorn api.main:app --reload --port 8000</code>
          </p>
        </div>
      </div>
    );
  }

  // ── Main render ────────────────────────────────────────────────────────────
  return (
    <div className="p-8 max-w-2xl">

      <div className="mb-8">
        <p className="font-mono text-[10px] tracking-[0.2em] uppercase mb-1" style={{ color: '#334155' }}>
          Configuration
        </p>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--text-1)' }}>
          Settings
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
          Your profile is used by the Apply Agent to fill out job applications.
        </p>
      </div>

      <form onSubmit={handleSave} className="space-y-4">

        {/* ── Personal Info ── */}
        <Section title="Personal Info" description="Used to fill out application forms automatically.">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Full Name">
              <Input value={form.full_name} onChange={set("full_name")} placeholder="Nick Perry" />
            </Field>
            <Field label="Email">
              <Input value={form.email} onChange={set("email")} placeholder="nick@example.com" type="email" />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Phone" hint="optional">
              <Input value={form.phone} onChange={set("phone")} placeholder="+1 (555) 000-0000" />
            </Field>
            <Field label="Location" hint="city or remote">
              <Input value={form.location} onChange={set("location")} placeholder="San Francisco, CA" />
            </Field>
          </div>
        </Section>

        {/* ── Online Presence ── */}
        <Section title="Online Presence" description="Links included on applications.">
          <Field label="LinkedIn URL">
            <Input value={form.linkedin_url} onChange={set("linkedin_url")} placeholder="https://linkedin.com/in/yourname" />
          </Field>
          <Field label="GitHub URL">
            <Input value={form.github_url} onChange={set("github_url")} placeholder="https://github.com/yourname" />
          </Field>
          <Field label="Portfolio / Website" hint="optional">
            <Input value={form.portfolio_url} onChange={set("portfolio_url")} placeholder="https://yourname.dev" />
          </Field>
        </Section>

        {/* ── Resume ── */}
        <Section title="Resume" description="Path to your resume PDF on this machine.">
          <Field label="Resume Path" hint="absolute path to PDF">
            <Input
              value={form.resume_path}
              onChange={set("resume_path")}
              placeholder="/Users/you/Desktop/job-agent/data/resumes/YourResume.pdf"
            />
          </Field>
          <p className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
            Place your resume in{" "}
            <code style={{ color: '#475569' }}>data/resumes/</code>
            {" "}and paste the full path above. The Resume Match Agent reads this file when scoring jobs.
          </p>
        </Section>

        {/* ── Job Preferences ── */}
        <Section title="Job Preferences" description="Controls which job types the Apply Agent targets.">
          <Toggle
            label="Target internships"
            description="Include internship and co-op listings"
            checked={form.target_internships}
            onChange={set("target_internships")}
          />
          <Toggle
            label="Target new grad roles"
            description="Include entry-level and new graduate positions"
            checked={form.target_new_grad}
            onChange={set("target_new_grad")}
          />
          <Field label="Auto-apply score threshold" hint="0–100, leave blank to use global default">
            <Input
              value={form.auto_apply_threshold ?? ""}
              onChange={v => set("auto_apply_threshold")(v === "" ? null : parseInt(v, 10))}
              placeholder="70 (global default)"
              type="number"
            />
          </Field>
        </Section>

        {/* ── Save ── */}
        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={saving}
            className="px-5 py-2.5 rounded-lg text-sm font-semibold transition-all"
            style={{
              backgroundColor: '#a78bfa',
              color: '#07090c',
              opacity: saving ? 0.5 : 1,
              cursor: saving ? 'not-allowed' : 'pointer',
            }}
            onMouseEnter={e => { if (!saving) e.currentTarget.style.backgroundColor = '#c4b5fd'; }}
            onMouseLeave={e => { e.currentTarget.style.backgroundColor = '#a78bfa'; }}
          >
            {saving ? "Saving…" : "Save Settings"}
          </button>
          {saved && (
            <span className="font-mono text-[10px] tracking-widest uppercase" style={{ color: '#34d399' }}>
              SAVED
            </span>
          )}
          {error && (
            <span className="text-sm font-mono" style={{ color: '#f87171' }}>{error}</span>
          )}
        </div>

      </form>
    </div>
  );
}
