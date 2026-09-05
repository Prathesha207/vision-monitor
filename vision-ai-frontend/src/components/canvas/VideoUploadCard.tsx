import React from 'react';
import { UploadCloud, Loader2, Play } from 'lucide-react';

interface VideoUploadCardProps {
  uploadProgress: number | null;
  isSelectingVideo: boolean;
  isBackendConnected: boolean;
  onSelectVideoAndStart: () => void;
}

export const VideoUploadCard: React.FC<VideoUploadCardProps> = ({
  uploadProgress,
  isSelectingVideo,
  isBackendConnected,
  onSelectVideoAndStart,
}) => {
  return (
    <div 
      className="absolute inset-0 w-full h-full p-2.5 sm:p-3.5 z-20 pointer-events-none flex flex-col items-center justify-center rounded-3xl"
      style={{ backgroundColor: 'var(--bg-card)' }}
    >
      <div 
        onClick={onSelectVideoAndStart}
        className="w-full h-full rounded-[14px] border-2 border-dashed border-[var(--border-color)] hover:border-[var(--accent-pond)] flex flex-col items-center justify-center text-center p-3 sm:p-6 lg:p-8 pointer-events-auto overflow-y-auto hidden-scrollbar cursor-pointer transition-colors"
        style={{ backgroundColor: 'var(--bg-card)' }}
      >
        <div className="text-[var(--accent-pond)] mb-2 sm:mb-3 shrink-0">
          <UploadCloud className="w-10 h-10 sm:w-12 sm:h-12 lg:w-14 lg:h-14 stroke-[1.75]" />
        </div>

        <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-[var(--text-primary)] mb-1 sm:mb-2 shrink-0">
          Select Video for Inference
        </h3>
        <p className="text-xs sm:text-sm lg:text-base text-[var(--text-secondary)] max-w-md mb-6 sm:mb-8 shrink-0">
          Click <span className="font-bold text-[var(--text-primary)]">Start Inference</span> above or click anywhere here to choose a video file.
        </p>

        {isSelectingVideo && uploadProgress === null && (
          <div className="flex items-center gap-2 mb-6 text-sm text-[var(--accent-pond)] font-medium">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Selecting video...</span>
          </div>
        )}

        {uploadProgress !== null && (
          <div className="w-full max-w-sm mb-5 sm:mb-6 shrink-0">
            <div className="flex items-center justify-between text-xs text-[var(--text-secondary)] mb-1.5 font-medium">
              <span className="flex items-center gap-1.5">
                <Loader2 className="w-4 h-4 animate-spin text-[var(--accent-pond)]" />
                Uploading video file...
              </span>
              <span className="font-bold font-mono text-[var(--accent-pond)]">
                {Math.round(uploadProgress)}%
              </span>
            </div>
            <div className="w-full h-2.5 bg-[var(--btn-secondary-border)] rounded-full overflow-hidden">
              <div
                className="h-full bg-[var(--accent-pond)] transition-all duration-150 rounded-full"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center justify-center gap-1.5 sm:gap-2.5 shrink-0">
          <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
            MP4
          </span>
          <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
            WebM
          </span>
          <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
            MOV
          </span>
          <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
            MKV
          </span>
        </div>
      </div>
    </div>
  );
};
