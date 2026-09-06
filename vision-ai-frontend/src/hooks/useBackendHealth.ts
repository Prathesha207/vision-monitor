import { useState, useEffect } from 'react';
import { getApiBaseUrl } from '../lib/api';
import { HEALTH_POLL_INTERVAL_MS } from '../utils/constants';

export const INITIALIZED_STORAGE_KEY = 'vision_monitor_initialized';

export function useBackendHealth() {
  // Read persisted initialization state (true if setup was completed in previous session)
  const isPreviouslyInitialized =
    typeof window !== 'undefined' && localStorage.getItem(INITIALIZED_STORAGE_KEY) === 'true';

  const [systemInitialized, setSystemInitialized] = useState<boolean>(isPreviouslyInitialized);
  const [isBackendConnected, setIsBackendConnected] = useState<boolean>(true);

  // Background health polling to keep status up to date
  useEffect(() => {
    let isMounted = true;
    const checkBackend = async () => {
      try {
        const res = await fetch(`${getApiBaseUrl()}/health`, { method: 'GET', cache: 'no-store' });
        if (isMounted) {
          setIsBackendConnected(res.ok);
          // If setup was previously completed and backend is healthy, ensure workspace is entered
          if (res.ok && localStorage.getItem(INITIALIZED_STORAGE_KEY) === 'true') {
            setSystemInitialized(true);
          }
        }
      } catch {
        if (isMounted) setIsBackendConnected(false);
      }
    };

    checkBackend();
    const interval = setInterval(checkBackend, HEALTH_POLL_INTERVAL_MS);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const handleInitializeSystem = () => {
    try {
      localStorage.setItem(INITIALIZED_STORAGE_KEY, 'true');
    } catch (e) {
      console.warn('Could not persist initialization state:', e);
    }
    setSystemInitialized(true);
  };

  return {
    systemInitialized,
    setSystemInitialized,
    isBackendConnected,
    setIsBackendConnected,
    handleInitializeSystem,
  };
}
