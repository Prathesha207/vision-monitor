import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { CameraConfig } from '../types';
import {
  Camera,
  Check,
  Cpu,
  Wifi,
  Loader2,
  Usb,
  RefreshCw,
  ArrowRight,
  CheckCircle2,
  Sliders,
} from 'lucide-react';
import { playWaterDropSound } from '../utils/audio';
import { Modal, Button } from './ui';
import { cameraService } from './service/cameraService';

interface CameraSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  config: CameraConfig;
  onSaveConfig: (newConfig: CameraConfig) => void;
  onReconnect: (configToConnect?: CameraConfig) => Promise<void> | void;
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
  const [isConnecting, setIsConnecting] = useState(false);
  const [discoveredDevices, setDiscoveredDevices] = useState<any[]>([]);
  const [savedCameras, setSavedCameras] = useState<any[]>([]);
  const [isScanning, setIsScanning] = useState(false);

  // Click any IP or device chip: automatically insert and connect immediately
  const handleSelectDeviceAndConnect = useCallback(async (ipOrId: string, name?: string) => {
    playWaterDropSound();
    const updated: CameraConfig = {
      ...localConfig,
      ipAddress: ipOrId,
      ...(name ? { sourceName: name } : {}),
      targetFps: localConfig.targetFps || 30,
    };
    setLocalConfig(updated);
    setIsConnecting(true);
    try {
      if (onReconnect) {
        await onReconnect(updated);
      }
    } finally {
      setIsConnecting(false);
    }
  }, [localConfig, onReconnect]);

  // Connect helper - automatically sets IP/target and initiates connection
  const handleConnect = useCallback(async (customConfig?: CameraConfig) => {
    playWaterDropSound();
    setIsConnecting(true);
    const target = customConfig || localConfig;
    try {
      if (onReconnect) {
        await onReconnect({
          ...target,
          targetFps: target.targetFps || 30,
        });
      }
    } finally {
      setIsConnecting(false);
    }
  }, [localConfig, onReconnect]);

  // Scan both DB registered cameras and physical DepthAI hardware devices
  const scanDevices = useCallback(async (autoConnectIfUsb: boolean = false) => {
    setIsScanning(true);
    try {
      // 1. Fetch DB configured cameras
      const dbCameras = await cameraService.checkCamera().catch(() => []);
      if (Array.isArray(dbCameras)) {
        setSavedCameras(dbCameras);
      }

      // 2. Discover physical DepthAI hardware devices over USB or network
      const hwDevices = await cameraService.getAvailableDevices().catch(() => []);
      if (Array.isArray(hwDevices)) {
        setDiscoveredDevices(hwDevices);

        // Auto-detect physical USB camera if found
        const usb = hwDevices.find((d: any) => d.is_usb);
        if (usb) {
          const usbTarget = usb.ip_or_id || 'usb';
          const usbName = usb.name || 'OAK USB Camera';
          setLocalConfig((prev) => {
            if (!prev.ipAddress || prev.ipAddress === '127.0.0.1') {
              return {
                ...prev,
                ipAddress: usbTarget,
                sourceName: prev.sourceName === 'Mock OAK Camera' ? usbName : prev.sourceName,
              };
            }
            return prev;
          });

          // Automatically connect to discovered USB camera if currently disconnected
          if (autoConnectIfUsb && !config.connected) {
            await handleSelectDeviceAndConnect(usbTarget, usbName);
          }
        }
      }
    } finally {
      setIsScanning(false);
    }
  }, [config.connected, onReconnect, handleSelectDeviceAndConnect]);

  useEffect(() => {
    if (isOpen) {
      setLocalConfig({
        ...config,
        targetFps: config.targetFps || 30,
      });

      // Always load latest camera config from DB on modal open
      cameraService.checkCamera().then((cameras) => {
        if (Array.isArray(cameras) && cameras.length > 0) {
          setSavedCameras(cameras);
          const active = cameras.find((c: any) => c.is_enabled) || cameras[0];
          if (active) {
            setLocalConfig((prev) => ({
              ...prev,
              id: active.id,
              sourceName: active.name || prev.sourceName,
              ipAddress: active.ip_address || '',
              resolution: (active.resolution as any) || prev.resolution || '1920x1080',
              targetFps: active.fps || 30,
              rotationAngle: active.rotation_angle ?? prev.rotationAngle,
              controlMode: (active.control_mode as any) ?? prev.controlMode,
              exposure: active.exposure ?? prev.exposure,
              gain: active.gain ?? prev.gain,
              iso: active.gain ?? prev.iso,
              focus: active.focus ?? prev.focus,
              brightness: active.brightness ?? prev.brightness,
              contrast: active.contrast ?? prev.contrast,
              autoFocus: active.auto_focus ?? prev.autoFocus,
              autoExposure: active.auto_exposure ?? prev.autoExposure,
            }));
          }
        }
      }).catch(() => {});

      // Scan hardware devices and auto-connect if physical USB camera is plugged in
      scanDevices(true);
    }
  }, [config, isOpen, scanDevices]);

  const handleSave = () => {
    playWaterDropSound();
    onSaveConfig(localConfig);
    onClose();
  };

  // Helper for theme-colored range slider fill track
  const getSliderStyle = (val: number, min: number, max: number) => {
    const pct = Math.max(0, Math.min(100, ((val - min) / (max - min)) * 100));
    return {
      background: `linear-gradient(to right, var(--accent-pond) 0%, var(--accent-pond) ${pct}%, var(--border-color) ${pct}%, var(--border-color) 100%)`,
      accentColor: 'var(--accent-pond)',
    };
  };

  // Available camera targets (discovered + registered + presets)
  const usbDevice = discoveredDevices.find((d: any) => d.is_usb);
  const availableTargets = useMemo(() => {
    const list: { name: string; ip: string; isUsb?: boolean; isOnline?: boolean }[] = [];
    const seen = new Set<string>();

    // 1. Discovered hardware devices
    discoveredDevices.forEach((d: any) => {
      const ip = d.ip_or_id || (d.is_usb ? 'usb' : '');
      if (ip && !seen.has(ip)) {
        seen.add(ip);
        list.push({
          name: d.name || (d.is_usb ? 'USB OAK Camera' : 'Network OAK'),
          ip,
          isUsb: d.is_usb,
          isOnline: true,
        });
      }
    });

    // 2. Saved cameras from database
    savedCameras.forEach((c: any) => {
      if (c.ip_address && !seen.has(c.ip_address)) {
        seen.add(c.ip_address);
        list.push({
          name: c.name || `Camera #${c.id}`,
          ip: c.ip_address,
          isUsb: c.ip_address.toLowerCase().includes('usb'),
          isOnline: c.is_enabled,
        });
      }
    });

    // 3. Fallback common presets
    if (!seen.has('192.168.1.100')) {
      list.push({ name: 'Default OAK PoE', ip: '192.168.1.100', isUsb: false });
    }
    if (!seen.has('127.0.0.1')) {
      list.push({ name: 'Local Mock', ip: '127.0.0.1', isUsb: false });
    }

    return list;
  }, [discoveredDevices, savedCameras]);

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
      <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
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
                className="w-full px-3 py-2 rounded-xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-xs font-medium text-[var(--text-primary)] focus:outline-hidden focus:border-[var(--accent-pond)]"
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
                        ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] border-transparent shadow-xs'
                        : 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-secondary)] hover:bg-[var(--btn-secondary-hover)]'
                      }`}
                  >
                    {res === '1920x1080' ? '1080p FHD' : '720p HD'}
                  </button>
                ))}
              </div>
            </div>

            {/* Target FPS Slider with Theme Color Track */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1.5">
                <span className="flex items-center gap-1.5">
                  <Sliders className="w-3.5 h-3.5 text-[var(--accent-pond)]" />
                  Target Framerate (FPS)
                </span>
                <span className="font-mono font-bold text-[var(--accent-pond)] bg-[var(--accent-pond-subtle)] px-2 py-0.5 rounded-md text-xs border border-[var(--border-color)]">
                  {localConfig.targetFps} FPS
                </span>
              </div>
              <input
                type="range"
                min={10}
                max={60}
                step={5}
                value={localConfig.targetFps}
                onChange={(e) => setLocalConfig({ ...localConfig, targetFps: parseInt(e.target.value, 10) })}
                style={getSliderStyle(localConfig.targetFps, 10, 60)}
                className="w-full h-2 rounded-lg cursor-pointer transition-all border border-[var(--border-color)]"
              />
            </div>

            {/* Neatly Styled USB / IP Camera Connection Card */}
            <div className="p-4 rounded-2xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)] flex flex-col gap-3.5 shadow-2xs">
              <style>{`
                @keyframes themeIndeterminateProgress {
                  0% { left: -35%; width: 35%; }
                  50% { left: 30%; width: 55%; }
                  100% { left: 100%; width: 35%; }
                }
              `}</style>

              {/* Header with Title and Connection Status */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-xl bg-[var(--accent-pond-subtle)] border border-[var(--border-color)] flex items-center justify-center text-[var(--accent-pond)] shadow-2xs">
                    {localConfig.ipAddress?.toLowerCase().includes('usb') ? (
                      <Usb className="w-4 h-4" />
                    ) : (
                      <Wifi className="w-4 h-4" />
                    )}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-[var(--text-primary)]">
                        Camera Connection
                      </span>
                      {localConfig.ipAddress?.toLowerCase().includes('usb') && (
                        <span className="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-[var(--accent-pond-subtle)] text-[var(--accent-pond)] border border-[var(--accent-pond)]/30">
                          USB MODE
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-[var(--text-muted)]">
                      Direct USB OAK device or network PoE / IP stream
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {/* Status Badge */}
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold flex items-center gap-1.5 border ${
                      isConnecting
                        ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30'
                        : localConfig.connected
                        ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                        : 'bg-[var(--bg-card)] text-[var(--text-muted)] border-[var(--border-color)]'
                    }`}
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${
                        isConnecting
                          ? 'bg-amber-500 animate-ping'
                          : localConfig.connected
                          ? 'bg-emerald-500'
                          : 'bg-stone-400'
                      }`}
                    />
                    {isConnecting ? 'Connecting...' : localConfig.connected ? 'Connected' : 'Standby'}
                  </span>

                  {/* Re-scan button */}
                  <button
                    type="button"
                    onClick={() => scanDevices(false)}
                    disabled={isScanning}
                    title="Scan hardware and network for connected cameras"
                    className="p-1.5 rounded-xl border border-[var(--border-color)] bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--accent-pond)] hover:border-[var(--accent-pond)] transition-all cursor-pointer shadow-2xs"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${isScanning ? 'animate-spin text-[var(--accent-pond)]' : ''}`} />
                  </button>
                </div>
              </div>

              {/* Automatic USB Camera Quick-Connect Banner if USB device is detected */}
              {usbDevice && (
                <div
                  onClick={() => handleSelectDeviceAndConnect(usbDevice.ip_or_id || 'usb', usbDevice.name)}
                  className="p-3 rounded-xl border border-[var(--accent-pond)]/50 bg-[var(--accent-pond-subtle)] flex items-center justify-between gap-3 cursor-pointer hover:border-[var(--accent-pond)] hover:shadow-xs transition-all group"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="w-7 h-7 rounded-lg bg-[var(--accent-pond)] text-white flex items-center justify-center shrink-0 shadow-2xs">
                      <Usb className="w-4 h-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold text-[var(--text-primary)] truncate">
                          {usbDevice.name || 'USB OAK Camera'}
                        </span>
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-[var(--accent-pond)] text-white">
                          USB READY
                        </span>
                      </div>
                      <span className="text-[10px] text-[var(--text-secondary)] block truncate">
                        Physical device detected &bull; Click to automatically connect
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="px-3 py-1.5 rounded-lg text-xs font-bold bg-[var(--accent-pond)] text-white shrink-0 group-hover:scale-105 transition-transform flex items-center gap-1 shadow-xs"
                  >
                    <span>Auto Connect</span>
                    <ArrowRight className="w-3 h-3" />
                  </button>
                </div>
              )}

              {/* Discovered / Available IP & Device Chips (Click to insert & connect immediately!) */}
              <div>
                <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5 block">
                  Available Targets (Click to insert &amp; connect):
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {/* USB Camera Option */}
                  <button
                    type="button"
                    onClick={() => handleSelectDeviceAndConnect(usbDevice?.ip_or_id || 'usb', 'OAK USB Camera')}
                    title="Insert 'usb' and connect directly via USB"
                    className={`px-2.5 py-1.5 rounded-xl text-xs font-medium border flex items-center gap-1.5 transition-all cursor-pointer ${
                      localConfig.ipAddress === 'usb' || localConfig.ipAddress === usbDevice?.ip_or_id
                        ? 'bg-[var(--accent-pond)] text-white border-transparent shadow-xs font-bold'
                        : 'bg-[var(--bg-card)] border-[var(--border-color)] text-[var(--text-primary)] hover:border-[var(--accent-pond)] hover:bg-[var(--accent-pond-subtle)]'
                    }`}
                  >
                    <Usb className="w-3.5 h-3.5" />
                    <span>USB Camera</span>
                    {usbDevice && (
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 ml-0.5" />
                    )}
                  </button>

                  {/* Discovered / Saved Targets */}
                  {availableTargets.map((item, idx) => {
                    const isCurrent = localConfig.ipAddress === item.ip;
                    return (
                      <button
                        key={`cam-target-${idx}-${item.ip}`}
                        type="button"
                        onClick={() => handleSelectDeviceAndConnect(item.ip, item.name)}
                        title={`Click to insert ${item.ip} and connect immediately`}
                        className={`px-2.5 py-1.5 rounded-xl text-xs font-medium border flex items-center gap-1.5 transition-all cursor-pointer ${
                          isCurrent
                            ? 'bg-[var(--accent-pond)] text-white border-transparent shadow-xs font-bold'
                            : 'bg-[var(--bg-card)] border-[var(--border-color)] text-[var(--text-primary)] hover:border-[var(--accent-pond)] hover:bg-[var(--accent-pond-subtle)]'
                        }`}
                      >
                        {item.isUsb ? <Usb className="w-3.5 h-3.5" /> : <Wifi className="w-3.5 h-3.5" />}
                        <span>{item.name}</span>
                        <span className="font-mono text-[10px] opacity-75">({item.ip})</span>
                        {item.isOnline && (
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 ml-0.5" />
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Target Input with Connect Button */}
              <div className="flex items-center gap-2 pt-2 border-t border-[var(--border-color)]/70">
                <div className="relative flex-1">
                  <input
                    type="text"
                    placeholder="e.g. 192.168.1.100 or usb"
                    value={localConfig.ipAddress}
                    onChange={(e) => setLocalConfig({ ...localConfig, ipAddress: e.target.value })}
                    className="w-full pl-3 pr-7 py-2 rounded-xl bg-[var(--bg-card)] border border-[var(--border-color)] text-xs font-mono font-medium text-[var(--text-primary)] focus:outline-hidden focus:border-[var(--accent-pond)] focus:ring-1 focus:ring-[var(--accent-pond)] transition-all"
                  />
                  {localConfig.ipAddress && (
                    <button
                      type="button"
                      onClick={() => setLocalConfig({ ...localConfig, ipAddress: '' })}
                      title="Clear address"
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs font-bold cursor-pointer"
                    >
                      &times;
                    </button>
                  )}
                </div>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={isConnecting}
                  onClick={() => handleConnect()}
                >
                  {isConnecting ? (
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Connecting...
                    </span>
                  ) : localConfig.connected ? (
                    'Reconnect'
                  ) : (
                    'Connect'
                  )}
                </Button>
              </div>

              {/* Theme Colored Connection Progress Bar */}
              {isConnecting && (
                <div className="w-full pt-1.5 space-y-1.5 animate-in fade-in duration-300">
                  <div className="relative w-full h-2 bg-[var(--bg-card)] rounded-full overflow-hidden border border-[var(--border-color)]">
                    <div
                      className="absolute top-0 bottom-0 rounded-full"
                      style={{
                        background: 'var(--accent-pond)',
                        boxShadow: '0 0 10px var(--accent-pond)',
                        animation: 'themeIndeterminateProgress 1.4s infinite ease-in-out',
                      }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[11px] font-semibold text-[var(--accent-pond)]">
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="w-3 h-3 animate-spin text-[var(--accent-pond)]" />
                      Connecting to {localConfig.ipAddress || 'USB Camera'}...
                    </span>
                    <span className="font-mono text-[10px] opacity-75">Configuring pipeline</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'image' && (
          <div className="space-y-4">
            {/* Brightness */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1">
                <span>Brightness</span>
                <span className="font-mono text-[var(--accent-pond)] font-bold">{localConfig.brightness}</span>
              </div>
              <input
                type="range"
                min={-50}
                max={50}
                value={localConfig.brightness}
                onChange={(e) => setLocalConfig({ ...localConfig, brightness: parseInt(e.target.value, 10) })}
                style={getSliderStyle(localConfig.brightness, -50, 50)}
                className="w-full h-2 rounded-lg cursor-pointer transition-all border border-[var(--border-color)]"
              />
            </div>

            {/* Contrast */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1">
                <span>Contrast</span>
                <span className="font-mono text-[var(--accent-pond)] font-bold">{localConfig.contrast}</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={localConfig.contrast}
                onChange={(e) => setLocalConfig({ ...localConfig, contrast: parseInt(e.target.value, 10) })}
                style={getSliderStyle(localConfig.contrast, 0, 100)}
                className="w-full h-2 rounded-lg cursor-pointer transition-all border border-[var(--border-color)]"
              />
            </div>

            {/* Exposure */}
            <div>
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)] mb-1">
                <span>Exposure Time</span>
                <span className="font-mono text-[var(--accent-pond)] font-bold">{localConfig.exposure} ms</span>
              </div>
              <input
                type="range"
                min={10}
                max={100}
                value={localConfig.exposure}
                onChange={(e) => setLocalConfig({ ...localConfig, exposure: parseInt(e.target.value, 10) })}
                style={getSliderStyle(localConfig.exposure, 10, 100)}
                className="w-full h-2 rounded-lg cursor-pointer transition-all border border-[var(--border-color)]"
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
