import React, { useRef, useState } from 'react';
import {
  Bot,
  ArrowRight,
  Zap,
  Sparkles,
  ShieldCheck,
  Cpu,
} from 'lucide-react';
import { playWaterDropSound } from '../utils/audio';

interface LandingScreenProps {
  onInitialize: (videoFile?: File) => void;
  cameraConnected: boolean | null;
}

export const LandingScreen: React.FC<LandingScreenProps> = ({
  onInitialize,
  cameraConnected,
}) => {
  const [isHovered, setIsHovered] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleStart = () => {
    playWaterDropSound();
    if (cameraConnected !== true) {
      fileInputRef.current?.click();
      return;
    }
    onInitialize();
  };

  const handleFileSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) {
      onInitialize(file);
    }
  };

  return (
    <div className="relative w-screen h-screen max-h-screen overflow-hidden bg-[var(--bg-page)] text-[var(--text-primary)] flex items-center justify-center p-4 sm:p-6 select-none transition-colors duration-300">
      <div className="absolute inset-0 pointer-events-none opacity-30 dark:opacity-20 bg-[radial-gradient(#3D6A52_1px,transparent_1px)] [background-size:28px_28px]" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] rounded-full bg-[var(--accent-pond)]/10 blur-3xl pointer-events-none" />

      <div className="relative z-10 w-full max-w-lg mx-auto flex flex-col items-center text-center animate-in fade-in zoom-in-95 duration-500 ease-out">
        <div className="relative flex items-center justify-center w-16 h-16 sm:w-20 sm:h-20 rounded-3xl bg-[var(--accent-pond-subtle)] text-[var(--accent-pond)] border border-[var(--border-color)] shadow-md mb-6 transition-transform duration-300 hover:scale-105">
          <Bot className="w-8 h-8 sm:w-10 sm:h-10" />
          <span className="absolute -bottom-1 -right-1 w-4 h-4 bg-emerald-500 rounded-full border-2 border-[var(--bg-page)] shadow-xs animate-pulse" />
        </div>

        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[var(--bg-card)] border border-[var(--border-color)] text-[var(--accent-pond)] text-xs font-bold tracking-wide shadow-2xs mb-4">
          <Sparkles className="w-3.5 h-3.5 text-[var(--accent-duck)]" />
          <span>VisionMonitor &bull; v4.2 PRO</span>
        </div>

        <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-[var(--text-primary)] leading-tight mb-3">
          Duck Detection &amp; <br />
          <span className="text-[var(--accent-pond)]">Anomaly Monitoring</span>
        </h1>

        <p className="text-sm sm:text-base text-[var(--text-secondary)] max-w-md mx-auto leading-relaxed mb-6 font-normal">
          Real-time YOLOv8 computer vision tracking, automated duck population counting, and instant anomaly alerts.
        </p>

        <div className="flex items-center justify-center gap-4 sm:gap-6 mb-8 text-xs text-[var(--text-secondary)] font-medium">
          <div className="flex items-center gap-1.5">
            <Cpu className="w-4 h-4 text-[var(--accent-pond)]" />
            <span>YOLOv8 Real-Time</span>
          </div>
          <span className="text-[var(--border-color)]">&bull;</span>
          <div className="flex items-center gap-1.5">
            <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            <span>Anomaly Safeguards</span>
          </div>
        </div>

        <button
          onClick={handleStart}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          className="w-full sm:w-auto min-w-[220px] px-8 py-3.5 rounded-2xl bg-[var(--btn-primary-bg)] hover:bg-[var(--btn-primary-hover)] text-[var(--btn-primary-text)] font-bold text-base tracking-wide shadow-lg hover:shadow-xl hover:shadow-[var(--accent-pond)]/20 transition-all duration-200 flex items-center justify-center gap-3 cursor-pointer active:scale-98 group"
        >
          <Zap className="w-5 h-5 fill-current" />
          <span>Initialize System</span>
          <ArrowRight className={`w-5 h-5 transition-transform duration-200 ${isHovered ? 'translate-x-1' : ''}`} />
        </button>

        <p className="text-[11px] text-[var(--text-muted)] mt-4">
          {cameraConnected === false ? 'Camera offline - choose a video to begin inference' : 'Click to load the live monitoring workspace'}
        </p>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="video/*"
        className="hidden"
        onChange={handleFileSelected}
      />
    </div>
  );
};
