import React from 'react';
import { CameraOff, Video, RotateCcw } from 'lucide-react';
import { playWaterDropSound } from '../../utils/audio';

interface CameraOfflineCardProps {
  onSwitchToVideo: () => void;
  onRetryConnection?: () => void;
  onCanvasClick: (e: React.MouseEvent<HTMLDivElement>) => void;
}

export const CameraOfflineCard: React.FC<CameraOfflineCardProps> = ({
  onSwitchToVideo,
  onRetryConnection,
  onCanvasClick
}) => {
  return (
    <div 
      onClick={onCanvasClick}
      className="absolute inset-0 w-full h-full flex flex-col items-center justify-center text-center p-6 bg-[#0B1814] text-white z-10"
    >
      <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl bg-stone-900/90 border border-rose-500/40 flex items-center justify-center mb-3 sm:mb-4 text-rose-400 shadow-md">
        <CameraOff className="w-7 h-7 sm:w-8 sm:h-8" />
      </div>
      
      <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-stone-900/90 border border-rose-500/40 text-rose-200 text-xs font-semibold mb-2.5 shadow-sm">
        <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse" />
        NO OAK CAMERA DETECTED
      </div>

      <h3 className="text-base sm:text-lg lg:text-xl font-bold text-white mb-1">
        OAK-D Hardware Offline
      </h3>
      <p className="text-xs sm:text-sm text-stone-400 max-w-md mb-5 leading-relaxed font-medium">
        No Luxonis OAK-D / USB camera is currently connected. Connect a DepthAI device or switch back to Video mode to run inference.
      </p>

      <div className="flex flex-wrap items-center justify-center gap-2.5">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onSwitchToVideo();
          }}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-700 hover:bg-emerald-600 text-white font-bold text-xs shadow-xs transition-all cursor-pointer active:scale-95"
        >
          <Video className="w-3.5 h-3.5" />
          <span>Switch to Video Mode</span>
        </button>
        {onRetryConnection && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              playWaterDropSound();
              onRetryConnection();
            }}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-stone-800 hover:bg-stone-700 border border-stone-700 text-stone-200 font-semibold text-xs transition-all cursor-pointer active:scale-95"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Retry Connection</span>
          </button>
        )}
      </div>
    </div>
  );
};
