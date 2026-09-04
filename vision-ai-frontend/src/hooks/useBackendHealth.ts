import { useState, useEffect } from 'react';
import { getApiBaseUrl } from '../lib/api';
import { HEALTH_POLL_INTERVAL_MS } from '../utils/constants';

export function useBackendHealth() {
  // 0. System initialization and live backend health tracking
  const [systemInitialized, setSystemInitialized] = useState<boolean>(false);
  const [isBackendConnected, setIsBackendConnected] = useState<boolean>(true);

  // Background health polling to keep main page status up to date
  useEffect(() => {
    let isMounted = true;
    const checkBackend = async () => {
      try {
        const res = await fetch(`${getApiBaseUrl()}/health`, { method: 'GET', cache: 'no-store' });
        if (isMounted) setIsBackendConnected(res.ok);
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
    setSystemInitialized(true);
  };

  return {
    systemInitialized,
    setSystemInitialized,
    isBackendConnected,
    setIsBackendConnected,
    handleInitializeSystem
  };
}
