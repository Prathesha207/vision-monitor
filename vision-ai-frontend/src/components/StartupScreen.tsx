import React, { useState, useEffect, useCallback, useRef } from 'react';
import { getApiBaseUrl } from '../lib/api';
import { Bot, AlertTriangle } from 'lucide-react';

interface StartupScreenProps {
  onReady: () => void;
}

const MAX_WAIT_MS = 30_000;   // 30 seconds before showing error
const POLL_INTERVAL_MS = 1000;  // poll every 1s

export function StartupScreen({ onReady }: StartupScreenProps) {
  const [status, setStatus] = useState<'loading' | 'error'>('loading');
  const [message, setMessage] = useState('Connecting to AI engine...');
  const [detailsVisible, setDetailsVisible] = useState(false);
  const startTimeRef = useRef(Date.now());
  const mountedRef = useRef(true);
  const [retryCount, setRetryCount] = useState(0);


  useEffect(() => {
    mountedRef.current = true;
    startTimeRef.current = Date.now();
    setStatus('loading');
    setMessage('Connecting to AI engine...');

    const pollHealth = async () => {
      if (!mountedRef.current) return;

      try {
        const res = await fetch(`${getApiBaseUrl()}/health`, {
          
        });

        if (res.ok) {
          const data = await res.json();
          if (data.status === 'ready' || data.status === 'starting') {
            if (data.status === 'starting') {
              setMessage(data.stage === 'loading_models' ? 'Loading AI models...' : 'Initializing inference engine...');
            } else {
              setMessage('AI Engine ready!');
              setTimeout(() => {
                if (mountedRef.current) onReady();
              }, 500);
              return;
            }
          }
        }
      } catch {
        // Backend not up yet
      }

      const elapsed = Date.now() - startTimeRef.current;

      // Use functional state updates to avoid dependency issues if we were using useCallback, 
      // but here we just read the current state directly or rely on the elapsed time
      if (elapsed > 15000) {
        setMessage('AI engine is taking longer than expected...');
      } else if (elapsed > 5000) {
        setMessage(prev => prev === 'Connecting to AI engine...' ? 'Starting local backend...' : prev);
      }

      if (elapsed > MAX_WAIT_MS) {
        if (mountedRef.current) {
          setStatus('error');
        }
        return;
      }

      if (mountedRef.current) {
        setTimeout(pollHealth, POLL_INTERVAL_MS);
      }
    };

    pollHealth();

    return () => {
      mountedRef.current = false;
    };
  }, [retryCount]);

  const handleRetry = () => {
    setDetailsVisible(false);
    setRetryCount(prev => prev + 1); // Triggers useEffect to restart polling
  };

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-[var(--bg-page)] text-[var(--text-primary)]">
      
      {/* Decorative background elements */}
      <div className="absolute top-0 left-0 w-full h-full overflow-hidden pointer-events-none opacity-40">
        <div className="absolute -top-1/4 -left-1/4 w-1/2 h-1/2 bg-[var(--accent-pond-subtle)] rounded-full blur-[120px]" />
        <div className="absolute -bottom-1/4 -right-1/4 w-1/2 h-1/2 bg-[var(--accent-pond-subtle)] rounded-full blur-[120px]" />
      </div>

      <div className="relative flex flex-col items-center bg-[var(--bg-card)] border border-[var(--border-color)] p-10 sm:p-12 rounded-[2rem] shadow-2xl max-w-[460px] w-[90%] z-10">
        
        {/* Logo / Title */}
        <div className="flex flex-col items-center gap-5 text-center mb-8">
          <div className={`relative flex items-center justify-center w-20 h-20 rounded-3xl ${status === 'error' ? 'bg-[var(--status-warn-bg)] text-[var(--status-warn-text)] shadow-[var(--status-warn-bg)]' : 'bg-[var(--accent-pond-subtle)] text-[var(--accent-pond)] shadow-[var(--accent-pond-subtle)]'} border-[3px] border-[var(--border-color)] shrink-0 shadow-lg`}>
            {status === 'error' ? <AlertTriangle className="w-10 h-10" strokeWidth={1.5} /> : <Bot className="w-10 h-10" strokeWidth={1.5} />}
            {status === 'loading' && <span className="absolute -bottom-1.5 -right-1.5 w-5 h-5 bg-[var(--accent-pond)] rounded-full border-[4px] border-[var(--bg-card)] animate-pulse" />}
          </div>
          <div>
            <h1 className="text-3xl font-extrabold text-[var(--text-primary)] tracking-tight">Vision Monitor</h1>
            <p className="text-sm font-medium text-[var(--accent-pond)] mt-1 tracking-wide uppercase">AI Inference Engine</p>
          </div>
        </div>

        {/* Status: Loading */}
        {status === 'loading' && (
          <div className="flex flex-col items-center w-full animate-in fade-in zoom-in duration-500">
            <div className="relative w-14 h-14 mb-6">
              <div className="absolute inset-0 rounded-full border-4 border-[var(--bg-card-subtle)]" />
              <div className="absolute inset-0 rounded-full border-4 border-transparent border-t-[var(--accent-pond)] animate-spin" />
            </div>

            <p className="text-lg font-semibold text-[var(--text-primary)] text-center">{message}</p>
          </div>
        )}

        {/* Status: Timeout / Error (Friendly Warning) */}
        {status === 'error' && (
          <div className="flex flex-col items-center w-full animate-in fade-in slide-in-from-bottom-4 duration-500">
            <p className="text-lg font-bold text-[var(--text-primary)] text-center">AI engine couldn't start</p>
            <p className="text-sm text-[var(--text-secondary)] mt-2 mb-6 text-center leading-relaxed">
              The local backend did not become available.
            </p>

            <button
              onClick={handleRetry}
              className="w-full py-3 bg-[var(--btn-primary-bg)] hover:bg-[var(--btn-primary-hover)] text-[var(--btn-primary-text)] rounded-xl font-bold transition-all shadow-md active:scale-[0.98] mb-3"
            >
              Retry
            </button>
            <button
              onClick={() => setDetailsVisible(!detailsVisible)}
              className="text-sm font-medium text-[var(--accent-pond)] hover:underline"
            >
              {detailsVisible ? 'Hide details' : 'View details'}
            </button>
            
            {detailsVisible && (
              <div className="mt-4 p-4 bg-[var(--bg-page)] rounded-lg text-xs text-[var(--text-secondary)] w-full text-left">
                <p className="font-semibold mb-2">Backend did not respond on the expected local endpoint.</p>
                <p className="mb-1">Possible causes:</p>
                <ul className="list-disc pl-5 space-y-1">
                  <li>Backend failed to start</li>
                  <li>Another application is using the selected port</li>
                  <li>Required ML files are missing</li>
                  <li>PyTorch failed to initialize</li>
                  <li>Application files were not extracted correctly</li>
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
