import axios from "axios";

/**
 * Safely sanitizes and validates a candidate URL string.
 * Ensures the result is a strictly valid origin (e.g. "http://localhost:8000" or "http://127.0.0.1:8000").
 * Returns null if the value is malformed or invalid.
 */
function sanitizeBaseUrl(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  let val = raw.trim();
  if (!val || val === 'undefined' || val === 'null' || val === '[object Object]') {
    return null;
  }
  // Strip trailing slashes
  val = val.replace(/\/+$/, '');

  // If protocol was omitted (e.g. "localhost:8000" or "192.168.1.100:8000"), prepend http://
  if (!val.startsWith('http://') && !val.startsWith('https://')) {
    val = `http://${val}`;
  }

  try {
    const parsed = new URL(val);
    if (!parsed.hostname || parsed.hostname === 'undefined' || parsed.hostname === 'null') {
      return null;
    }
    const portStr = parsed.port ? `:${parsed.port}` : '';
    return `${parsed.protocol}//${parsed.hostname}${portStr}`;
  } catch {
    return null;
  }
}

/**
 * Dynamically resolves the Backend API URL:
 * 1. User preference in localStorage (if set and valid).
 * 2. Explicit environment variable `VITE_API_BASE_URL` (if set in .env or build).
 * 3. In browser: dynamically matches current hostname on port 8000.
 * 4. Fallback default: http://127.0.0.1:8000
 */
export function getApiBaseUrl(): string {
  // 1. User saved preference in localStorage (if set and valid)
  if (typeof window !== 'undefined') {
    try {
      const saved = localStorage.getItem('vision_backend_url');
      if (saved) {
        const sanitized = sanitizeBaseUrl(saved);
        if (sanitized) return sanitized;
        // Clean up corrupt/stale key
        localStorage.removeItem('vision_backend_url');
      }
    } catch { }
  }

  // 2. Explicit env override (e.g. from .env file or Cloudflare build)
  const envUrl = (import.meta as any).env?.VITE_API_BASE_URL;
  if (envUrl) {
    const sanitizedEnv = sanitizeBaseUrl(envUrl);
    if (sanitizedEnv) {
      if (typeof window !== 'undefined' && window.location?.hostname && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
        try {
          const parsed = new URL(sanitizedEnv);
          if (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1') {
            return `${window.location.protocol}//${window.location.hostname}:${parsed.port || '8000'}`;
          }
        } catch { }
      }
      return sanitizedEnv;
    }
  }

  // 3. Dynamic match of current browser host (e.g. http://localhost:5173 or LAN IP)
  if (typeof window !== 'undefined' && window.location?.hostname && window.location.protocol.startsWith('http')) {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
    const host = window.location.hostname;
    const dynamicCandidate = sanitizeBaseUrl(`${protocol}//${host}:8000`);
    if (dynamicCandidate) return dynamicCandidate;
  }

  // 4. Fallback default
  return "http://127.0.0.1:8000";
}

export const API_BASE_URL = getApiBaseUrl();

// BUGFIX: video_router.py's /video/upload runs an ffmpeg transcode subprocess
// with a 180s budget (see `timeout=180` in run_transcode()). A 15s axios
// timeout here means any upload that needs more than 15s to transcode gets
// aborted client-side while the backend keeps working in the background and
// eventually finishes into a session the UI has already given up on -- which
// looks identical to "stuck" or "offline" to the user. Raised to comfortably
// exceed the backend's own budget. If a specific call genuinely needs to fail
// fast (e.g. a lightweight status check), override per-call with
// `api.get(url, { timeout: 5000 })` rather than lowering this default.
export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 200000,
  headers: {
    "Content-Type": "application/json",
  },
});

// Dynamic interceptor: ensures API calls always resolve to current host/tunnel
api.interceptors.request.use((config) => {
  config.baseURL = getApiBaseUrl();
  return config;
});

export default api;