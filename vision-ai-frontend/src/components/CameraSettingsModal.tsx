import React, { useState } from 'react';
import { CameraConfig } from '../types';
import {
  Camera,
  Check,
  Cpu,
  Wifi
} from 'lucide-react';
import { playWaterDropSound } from '../utils/audio';
import { Modal, Button } from './ui';

interface CameraSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  config: CameraConfig;
  onSaveConfig: (newConfig: CameraConfig) => void;
  onReconnect: () => void;
}

export const CameraSettingsModal: React.FC<CameraSettingsModalProps> = ({
  isOpen,
  onClose,
  config,
  onSaveConfig,
  onReconnect,
}) => {
  const [localConfig, setLocalConfig] = useState<CameraConfig>({ ...config });
  const [activeTab, setActiveTab] = useState<'stream' | 'image' | 'oak'>('stream');

  React.useEffect(() => {
    if (isOpen) {
      setLocalConfig({ ...config });
    }
  }, [config, isOpen]);

  const handleSave = () => {
    playWaterDropSound();
    onSaveConfig(localConfig);
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      maxWidth="xl"
      icon={<Camera className="w-4 h-4 text-[var(--accent-pond)]" />}
      title="Camera & Stream Settings"
      description="Configure Luxonis OAK-D / Real-time Video Stream"
      footer={
        <>
          <Button
            variant="ghost"
            size="md"
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            size="md"
            onClick={handleSave}
            icon={<Check className="w-3.5 h-3.5" />}
          >
            Apply Changes
          </Button>
        </>
      }
    >
      {/* Tab Switcher */}
      <div className="flex border-b border-[var(--border-color)] pb-2 mb-4 -mt-1">
        <button
          onClick={() => setActiveTab('stream')}
          className={`pb-1.5 px-3 text-xs font-bold border-b-2 transition-all cursor-pointer ${activeTab === 'stream'
              ? 'border-[var(--accent-pond)] text-[var(--accent-pond)]'
              : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
        >
          Stream &amp; FPS
        </button>
        <button
          onClick={() => setActiveTab('image')}
          className={`pb-1.5 px-3 text-xs font-bold border-b-2 transition-all cursor-pointer ${activeTab === 'image'
              ? 'border-[var(--accent-pond)] text-[var(--accent-pond)]'
              : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
        >
          Image Adjustments
        </button>
        <button
          onClick={() => setActiveTab('oak')}
          className={`pb-1.5 px-3 text-xs font-bold border-b-2 transition-all cursor-pointer ${activeTab === 'oak'
              ? 'border-[var(--accent-pond)] text-[var(--accent-pond)]'
              : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
        >
          OAK DepthAI VPU
        </button>
      </div>

      {/* Modal Body */}
      <div className="space-y-4 max-h-[60vh] overflow-y-auto">
        {activeTab === 'stream' && (
          <div className="space-y-4">
            {/* Camera Name */}
            <div>
              <label className="block text-xs font-bold text-[var(--text-primary)] mb-1.5">
                Camera Name
              </label>
              <input
                type="text"
                placeholder="e.g., OAK Camera 1"
                value={localConfig.sourceName}
                onChange={(e) => setLocalConfig({ ...localConfig, sourceName: e.target.value })}
                className="w-full px-3 py-2 rounded-xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-xs font-medium text-[var(--text-primary)] focus:outline-hidden"
              />
            </div>



            {/* Resolution */}
            <div>
              <label className="block text-xs font-bold text-[var(--text-primary)] mb-1.5">
                Stream Resolution
              </label>
              <div className="grid grid-cols-2 gap-2">
                {(['1920x1080', '1280x720'] as const).map((res) => (
                  <button
                    key={res}
                    type="button"
                    onClick={() => setLocalConfig({ ...localConfig, resolution: res })}
                    className={`py-2 rounded-xl text-xs font-bold border transition-all cursor-pointer ${localConfig.resolution === res
                        ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] border-transparent'
                        : 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-secondary)] hover:bg-[var(--btn-secondary-hover)]'
                      }`}
                  >
                    {res === '1920x1080' ? '1080p FHD' : '720p HD'}
                  </button>
                ))}
              </div>
            </div>

            {/* Target FPS Slider */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1.5">
                <span>Target Framerate (FPS)</span>
                <span className="font-mono text-[var(--accent-pond)]">{localConfig.targetFps} FPS</span>
              </div>
              <input
                type="range"
                min={10}
                max={60}
                step={5}
                value={localConfig.targetFps}
                onChange={(e) => setLocalConfig({ ...localConfig, targetFps: parseInt(e.target.value, 10) })}
                className="w-full accent-[var(--accent-pond)] cursor-pointer"
              />
            </div>

            {/* USB device ID or network address */}
            <div className="p-3 rounded-2xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)] flex flex-col gap-2">
              <label className="flex items-center gap-2 text-xs font-bold text-[var(--text-primary)]">
                <Wifi className="w-4 h-4 text-[var(--accent-pond)]" />
                USB Device ID / IP Address
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="text"
                  placeholder="USB: 1749622413 or network: 192.168.1.100"
                  value={localConfig.ipAddress}
                  onChange={(e) => setLocalConfig({ ...localConfig, ipAddress: e.target.value })}
                  className="flex-1 px-3 py-2 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs font-medium text-[var(--text-primary)] focus:outline-hidden"
                />
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    playWaterDropSound();
                    onReconnect();
                  }}
                >
                  Reconnect
                </Button>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'image' && (
          <div className="space-y-4">
            {/* Brightness */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1">
                <span>Brightness</span>
                <span className="font-mono text-[var(--text-primary)]">{localConfig.brightness}</span>
              </div>
              <input
                type="range"
                min={-50}
                max={50}
                value={localConfig.brightness}
                onChange={(e) => setLocalConfig({ ...localConfig, brightness: parseInt(e.target.value, 10) })}
                className="w-full accent-[var(--accent-pond)] cursor-pointer"
              />
            </div>

            {/* Contrast */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1">
                <span>Contrast</span>
                <span className="font-mono text-[var(--text-primary)]">{localConfig.contrast}</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={localConfig.contrast}
                onChange={(e) => setLocalConfig({ ...localConfig, contrast: parseInt(e.target.value, 10) })}
                className="w-full accent-[var(--accent-pond)] cursor-pointer"
              />
            </div>

            {/* Exposure */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1">
                <span>Exposure Time</span>
                <span className="font-mono text-[var(--text-primary)]">{localConfig.exposure} ms</span>
              </div>
              <input
                type="range"
                min={10}
                max={100}
                value={localConfig.exposure}
                onChange={(e) => setLocalConfig({ ...localConfig, exposure: parseInt(e.target.value, 10) })}
                className="w-full accent-[var(--accent-pond)] cursor-pointer"
              />
            </div>

            {/* Auto Focus */}
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs font-bold text-[var(--text-primary)]">
                Continuous Pond Auto-Focus
              </span>
              <button
                type="button"
                onClick={() => setLocalConfig({ ...localConfig, autoFocus: !localConfig.autoFocus })}
                className={`w-11 h-6 flex items-center rounded-full p-1 transition-colors cursor-pointer ${localConfig.autoFocus ? 'bg-[var(--accent-pond)]' : 'bg-[var(--btn-secondary-border)]'
                  }`}
              >
                <span
                  className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${localConfig.autoFocus ? 'translate-x-5' : 'translate-x-0'
                    }`}
                />
              </button>
            </div>
          </div>
        )}

        {activeTab === 'oak' && (
          <div className="space-y-3">
            <div className="p-3 rounded-2xl bg-[var(--accent-pond-subtle)] border border-[var(--border-color)] text-xs leading-relaxed text-[var(--text-primary)]">
              <div className="font-bold flex items-center gap-1.5 text-[var(--accent-pond)] mb-1">
                <Cpu className="w-4 h-4" />
                On-Device VPU Neural Engine
              </div>
              The YOLOv8-DuckTracker model runs entirely on the Myriad X / Keem Bay VPU inside the OAK camera, outputting bounding box coordinates directly at zero CPU overhead.
            </div>

            <div className="space-y-2 text-xs">
              <div className="flex justify-between py-1.5 border-b border-[var(--border-color)]">
                <span className="text-[var(--text-secondary)]">Pipeline Mode</span>
                <span className="font-mono font-bold text-[var(--accent-pond)]">DepthAI Spatial Detection</span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-[var(--border-color)]">
                <span className="text-[var(--text-secondary)]">Device Temperature</span>
                <span className="font-mono font-bold text-[var(--text-primary)]">41.2 °C (Normal)</span>
              </div>
              <div className="flex justify-between py-1.5">
                <span className="text-[var(--text-secondary)]">Inference Engine Latency</span>
                <span className="font-mono font-bold text-[var(--text-primary)]">6.4 ms / frame</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
};
