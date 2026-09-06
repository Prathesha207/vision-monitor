import React from 'react';
import { Camera, Play, Video } from 'lucide-react';
import { playWaterDropSound } from '../../utils/audio';

interface CameraStandbyCardProps {
  onStartStream?: () => void;
  onSwitchToVideo?: () => void;
  onCanvasClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
}

export const CameraStandbyCard: React.FC<CameraStandbyCardProps> = ({
  onStartStream,
  onSwitchToVideo,
  onCanvasClick,
}) => {
  return (
    <div
      onClick={onCanvasClick}
      className="absolute inset-0 w-full h-full flex flex-col items-center justify-center text-center p-6 rounded-3xl z-10 select-none"
      style={{ backgroundColor: 'var(--bg-card)' }}
    >
      <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl bg-sky-500/10 border border-sky-500/30 flex items-center justify-center mb-3 sm:mb-4 text-sky-400 shadow-md">
        <Camera className="w-7 h-7 sm:w-8 sm:h-8" />
      </div>

      <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold mb-2.5 shadow-sm">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
        OAK-D CAMERA CONNECTED
      </div>

      <h3 className="text-base sm:text-lg lg:text-xl font-bold text-[var(--text-primary)] mb-1">
        Camera Feed in Standby
      </h3>
      <p className="text-xs sm:text-sm text-[var(--text-secondary)] max-w-md mb-5 leading-relaxed font-medium">
        Hardware connection is active. Start the camera stream to view live video, record sessions, or run real-time AI inference.
      </p>

      <div className="flex flex-wrap items-center justify-center gap-2.5">
        {onStartStream && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              playWaterDropSound();
              onStartStream();
            }}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs shadow-sm transition-all cursor-pointer active:scale-95"
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            <span>Start Camera Stream</span>
          </button>
        )}

        {onSwitchToVideo && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              playWaterDropSound();
              onSwitchToVideo();
            }}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[var(--btn-secondary-bg)] hover:bg-[var(--btn-secondary-hover)] border border-[var(--btn-secondary-border)] text-[var(--btn-secondary-text)] font-semibold text-xs transition-all cursor-pointer active:scale-95"
          >
            <Video className="w-3.5 h-3.5" />
            <span>Switch to Video</span>
          </button>
        )}
      </div>
    </div>
  );
};
