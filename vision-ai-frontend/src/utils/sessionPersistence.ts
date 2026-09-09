/**
 * Persists and restores the active inference session across page refreshes.
 *
 * Only sessionStorage is used — it survives Ctrl+R but is cleared when the
 * browser tab / Electron window is actually closed, so a genuine new launch
 * always starts fresh.
 */

const KEY = 'vision_monitor_session_state';

export interface PersistedSessionState {
  isRunning: boolean;
  sourceType: string;
  videoSessionId: string | null;
  customVideoUrl: string | undefined;
  customVideoName: string | undefined;
  expectedDucks?: number;
  framesProcessed?: number;
  fps?: number;
  uptimeSeconds?: number;
  ducks?: any[];
  stats?: any;
  selectedDuckId?: string | null;
  videoDimensions?: { width: number; height: number } | null;
  lastCameraFrame?: string;
  lastVideoFrame?: string;
}

export function saveSessionState(state: Partial<PersistedSessionState>) {
  try {
    const existing = loadSessionState() ?? ({} as PersistedSessionState);
    const merged = { ...existing, ...state };
    sessionStorage.setItem(KEY, JSON.stringify(merged));
  } catch {
    // If quota exceeded (e.g. from large base64 thumbnails), strip heavy fields and retry
    try {
      const existing = loadSessionState() ?? ({} as PersistedSessionState);
      const fallbackState = { ...existing, ...state };
      if (Array.isArray(fallbackState.ducks)) {
        fallbackState.ducks = fallbackState.ducks.map((d: any) => {
          if (!d) return d;
          const { thumbnail, ...rest } = d;
          return rest;
        });
      }
      if (fallbackState.stats && Array.isArray(fallbackState.stats.thumbnails)) {
        fallbackState.stats = { ...fallbackState.stats, thumbnails: [] };
      }
      sessionStorage.setItem(KEY, JSON.stringify(fallbackState));
    } catch {}
  }
}

export function loadSessionState(): PersistedSessionState | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    return JSON.parse(raw) as PersistedSessionState;
  } catch {
    return null;
  }
}

export function clearSessionState() {
  try {
    sessionStorage.removeItem(KEY);
  } catch { }
}
