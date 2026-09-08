import React, { useState, useEffect, useCallback, useRef } from 'react';
import { CameraConfig } from '../types';
import {
  Camera,
  Check,
  Cpu,
  Wifi,
  Loader2,
  Usb,
  RefreshCw,
  Trash2,
  Plus,
  Sliders,
  CheckCircle2,
  AlertCircle,
  X,
} from 'lucide-react';
import { playWaterDropSound } from '../utils/audio';
import { Modal, Button } from './ui';
import { cameraService, type CameraData } from './service/cameraService';

interface CameraSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  config: CameraConfig;
  onSaveConfig: (newConfig: CameraConfig) => void;
  onReconnect: (configToConnect?: CameraConfig) => Promise<void> | void;
}

interface SavedCameraRow {
  id: number;
  name: string;
  ip_address: string;
  resolution?: string;
  fps?: number;
  is_enabled?: boolean;
}

interface HwDevice {
  mxid: string;
  name: string;
  ip_or_id: string;
  is_usb: boolean;
}

const isUsbAddress = (ip?: string) => !ip || ip.toLowerCase().includes('usb') || /^[0-9a-f-]{8,}$/i.test(ip || '');

export const CameraSettingsModal: React.FC<CameraSettingsModalProps> = ({
  isOpen,
  onClose,
  config,
  onSaveConfig,
  onReconnect,
}) => {
  const [localConfig, setLocalConfig] = useState<CameraConfig>({ ...config });
  const [activeTab, setActiveTab] = useState<'stream' | 'image' | 'oak'>('stream');

  const [savedCameras, setSavedCameras] = useState<SavedCameraRow[]>([]);
  const [hwDevices, setHwDevices] = useState<HwDevice[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [isLoadingList, setIsLoadingList] = useState(false);

  const [connectingId, setConnectingId] = useState<number | 'new' | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [activeCameraId, setActiveCameraId] = useState<number | null>(null);

  // ---- Error tracking for user visibility ----
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [errorCameraId, setErrorCameraId] = useState<number | null>(null);

  // ---- Add-camera form ----
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState<'usb' | 'ip'>('usb');
  const [newIp, setNewIp] = useState('');
  const [detectedUsb, setDetectedUsb] = useState<HwDevice | null>(null);

  const onReconnectRef = useRef(onReconnect);
  onReconnectRef.current = onReconnect;

  // ---- Load saved cameras + do a hardware scan (no auto-connect) ----
  const refreshAll = useCallback(async () => {
    setIsLoadingList(true);
    try {
      const cameras = await cameraService.checkCamera().catch(() => []);
      if (Array.isArray(cameras)) setSavedCameras(cameras);
    } finally {
      setIsLoadingList(false);
    }
  }, []);

  const scanUsbDevices = useCallback(async () => {
    setIsScanning(true);
    try {
      const devices = await cameraService.getAvailableDevices().catch(() => []);
      const list: HwDevice[] = Array.isArray(devices) ? devices : [];
      setHwDevices(list);
      const usb = list.find((d) => d.is_usb) || null;
      setDetectedUsb(usb);
      return usb;
    } finally {
      setIsScanning(false);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) {
      setShowAddForm(false);
      setNewName('');
      setNewIp('');
      setDetectedUsb(null);
      setErrorMessage(null);
      setErrorCameraId(null);
      return;
    }
    setLocalConfig({ ...config, targetFps: config.targetFps || 30 });
    // ONLY set as active if the camera hardware is genuinely connected and online
    if (config.connected && config.id) {
      setActiveCameraId(config.id);
    } else {
      setActiveCameraId(null);
    }
    refreshAll();
    scanUsbDevices();
  }, [isOpen, config.connected, config.id]);

  // ---- Connect to a SAVED camera row ----
  const handleConnectSaved = async (row: SavedCameraRow) => {
    playWaterDropSound();
    setConnectingId(row.id);
    setErrorMessage(null);
    setErrorCameraId(null);
    const updated: CameraConfig = {
      ...localConfig,
      id: row.id,
      sourceName: row.name,
      ipAddress: row.ip_address,
      resolution: (row.resolution as any) || localConfig.resolution || '1920x1080',
      targetFps: row.fps || localConfig.targetFps || 30,
    };
    setLocalConfig(updated);
    try {
      await onReconnectRef.current(updated);
      setActiveCameraId(row.id);
      setErrorMessage(null);
      setErrorCameraId(null);
    } catch (err: any) {
      setActiveCameraId(null);
      setErrorCameraId(row.id);
      setErrorMessage(err?.message || 'Failed to connect to camera device');
    } finally {
      setConnectingId(null);
    }
  };

  // ---- Direct one-click delete (no window.confirm popup) ----
  const handleDelete = async (row: SavedCameraRow) => {
    playWaterDropSound();
    setDeletingId(row.id);
    try {
      await cameraService.deleteCamera(row.id);
      setSavedCameras((prev) => prev.filter((c) => c.id !== row.id));
      if (activeCameraId === row.id) setActiveCameraId(null);
      if (errorCameraId === row.id) {
        setErrorCameraId(null);
        setErrorMessage(null);
      }
    } catch (e) {
      console.error('Failed to delete camera:', e);
    } finally {
      setDeletingId(null);
    }
  };

  // ---- Detect USB device for the add-form ----
  const handleDetectUsb = async () => {
    playWaterDropSound();
    setErrorMessage(null);
    const usb = await scanUsbDevices();
    if (usb && !newName.trim()) {
      setNewName(usb.name || 'USB Camera');
    }
  };

  // ---- Create + connect the new camera ----
  const handleAddAndConnect = async () => {
    const name = newName.trim() || (newType === 'usb' ? 'USB Camera' : `IP Camera (${newIp})`);
    const ipAddress = newType === 'usb' ? (detectedUsb?.ip_or_id || 'usb') : newIp.trim();

    if (newType === 'ip' && !ipAddress) return;

    playWaterDropSound();
    setConnectingId('new');
    setErrorMessage(null);
    setErrorCameraId(null);
    try {
      const payload: CameraData = {
        name,
        ip_address: ipAddress,
        resolution: localConfig.resolution || '1920x1080',
        fps: localConfig.targetFps || 30,
        control_mode: 'auto',
      };
      const saved = await cameraService.createCamera(payload);
      const updated: CameraConfig = {
        ...localConfig,
        id: saved?.id,
        sourceName: name,
        ipAddress,
      };
      setLocalConfig(updated);
      await onReconnectRef.current(updated);
      setActiveCameraId(saved?.id ?? null);
      await refreshAll();
      setShowAddForm(false);
      setNewName('');
      setNewIp('');
      setDetectedUsb(null);
      setErrorMessage(null);
    } catch (err: any) {
      setActiveCameraId(null);
      setErrorMessage(err?.message || 'Failed to connect to new camera device');
    } finally {
      setConnectingId(null);
    }
  };

  const handleSave = () => {
    playWaterDropSound();
    onSaveConfig(localConfig);
    onClose();
  };

  const getSliderStyle = (val: number, min: number, max: number) => {
    const pct = Math.max(0, Math.min(100, ((val - min) / (max - min)) * 100));
    return {
      background: `linear-gradient(to right, var(--accent-pond) 0%, var(--accent-pond) ${pct}%, var(--border-color) ${pct}%, var(--border-color) 100%)`,
      accentColor: 'var(--accent-pond)',
    };
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      maxWidth="xl"
      icon={<Camera className="w-4 h-4 text-[var(--accent-pond)]" />}
      title="Camera & Stream Settings"
      description="Manage connected cameras and video pipeline"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={handleSave} icon={<Check className="w-3.5 h-3.5" />}>
            Apply Changes
          </Button>
        </>
      }
    >
      {/* Tab Switcher */}
      <div className="flex border-b border-[var(--border-color)] pb-2 mb-3 -mt-1 shrink-0">
        {(['stream', 'image', 'oak'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`pb-1.5 px-3 text-xs font-bold border-b-2 transition-all cursor-pointer ${
              activeTab === tab
                ? 'border-[var(--accent-pond)] text-[var(--accent-pond)]'
                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
          >
            {tab === 'stream' ? 'Cameras' : tab === 'image' ? 'Image Adjustments' : 'OAK DepthAI VPU'}
          </button>
        ))}
      </div>

      {/* Body content — Single scroll region */}
      <div className="space-y-3.5 pr-0.5">
        {activeTab === 'stream' && (
          <div className="space-y-3.5">
            {/* ── 1. STREAM CONFIGURATION (Compact Row) ── */}
            <div>
              <div className="text-[10px] font-black uppercase tracking-wider text-[var(--text-muted)] mb-1.5 px-0.5">
                Stream
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 p-2.5 rounded-2xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)]">
                {/* Resolution Selector */}
                <div className="space-y-1">
                  <label className="text-[11px] font-bold text-[var(--text-secondary)]">Resolution</label>
                  <div className="grid grid-cols-2 gap-1.5">
                    {(['1920x1080', '1280x720'] as const).map((res) => (
                      <button
                        key={res}
                        type="button"
                        onClick={() => setLocalConfig({ ...localConfig, resolution: res })}
                        className={`py-1.5 px-2 rounded-xl text-xs font-bold border transition-all cursor-pointer ${
                          localConfig.resolution === res
                            ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] border-transparent shadow-xs'
                            : 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-secondary)] hover:bg-[var(--btn-secondary-hover)]'
                        }`}
                      >
                        {res === '1920x1080' ? '1080p FHD' : '720p HD'}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Framerate Slider */}
                <div className="space-y-1 flex flex-col justify-between">
                  <div className="flex items-center justify-between text-[11px] font-bold text-[var(--text-secondary)]">
                    <span className="flex items-center gap-1">
                      <Sliders className="w-3 h-3 text-[var(--accent-pond)]" />
                      FPS
                    </span>
                    <span className="font-mono font-bold text-[var(--accent-pond)] bg-[var(--accent-pond-subtle)] px-1.5 py-0.5 rounded text-[11px] border border-[var(--border-color)]">
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
              </div>
            </div>

            {/* ── 2. YOUR CAMERAS (Dense List) ── */}
            <div>
              <div className="flex items-center justify-between mb-1.5 px-0.5">
                <span className="text-[10px] font-black uppercase tracking-wider text-[var(--text-muted)]">
                  Your Cameras {savedCameras.length > 0 && `(${savedCameras.length})`}
                </span>
                <button
                  type="button"
                  onClick={() => refreshAll()}
                  disabled={isLoadingList}
                  title="Refresh device list"
                  className="p-1 rounded-lg border border-[var(--border-color)] bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--accent-pond)] hover:border-[var(--accent-pond)] transition-all cursor-pointer"
                >
                  <RefreshCw className={`w-3 h-3 ${isLoadingList ? 'animate-spin text-[var(--accent-pond)]' : ''}`} />
                </button>
              </div>

              {savedCameras.length === 0 && !isLoadingList && (
                <div className="p-3.5 rounded-2xl border border-dashed border-[var(--border-color)] text-center text-xs text-[var(--text-muted)]">
                  No cameras registered. Add one below to get started.
                </div>
              )}

              <div className="space-y-1.5">
                {savedCameras.map((row) => {
                  const usb = isUsbAddress(row.ip_address);
                  const isActive = activeCameraId === row.id && Boolean(config.connected);
                  const isConnecting = connectingId === row.id;
                  const isRowFailed = errorCameraId === row.id;

                  return (
                    <div
                      key={row.id}
                      className={`p-2.5 rounded-xl border flex flex-col gap-1.5 transition-all ${
                        isRowFailed
                          ? 'border-rose-500/50 bg-rose-500/5'
                          : isActive
                          ? 'border-emerald-500/40 bg-emerald-500/5'
                          : 'border-[var(--border-color)] bg-[var(--bg-card)]'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2.5">
                        {/* Camera Info */}
                        <div className="flex items-center gap-2 min-w-0">
                          <div
                            className={`w-7 h-7 rounded-lg border flex items-center justify-center shrink-0 ${
                              isRowFailed
                                ? 'bg-rose-500/15 border-rose-500/30 text-rose-500'
                                : 'bg-[var(--accent-pond-subtle)] border-[var(--border-color)] text-[var(--accent-pond)]'
                            }`}
                          >
                            {usb ? <Usb className="w-3.5 h-3.5" /> : <Wifi className="w-3.5 h-3.5" />}
                          </div>
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <span className="text-xs font-bold text-[var(--text-primary)] truncate max-w-[140px] sm:max-w-[200px]">
                                {row.name}
                              </span>
                              {isActive ? (
                                <span className="inline-flex items-center gap-1 px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 shrink-0">
                                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                                  ACTIVE
                                </span>
                              ) : isRowFailed ? (
                                <span className="inline-flex items-center gap-1 px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30 shrink-0">
                                  <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
                                  OFFLINE
                                </span>
                              ) : (
                                <span className="px-1.5 py-0.2 rounded text-[9px] font-mono font-semibold bg-slate-500/10 text-[var(--text-muted)] border border-[var(--border-color)] shrink-0">
                                  DISCONNECTED
                                </span>
                              )}
                            </div>
                            <span className="text-[10px] text-[var(--text-muted)] font-mono block truncate">
                              {usb ? 'USB' : row.ip_address} · {row.resolution || '1920x1080'} · {row.fps || 30}fps
                            </span>
                          </div>
                        </div>

                        {/* Actions */}
                        <div className="flex items-center gap-1 shrink-0">
                          <Button
                            variant={isActive ? 'secondary' : 'primary'}
                            size="sm"
                            disabled={isConnecting}
                            onClick={() => handleConnectSaved(row)}
                            className="h-7 text-xs px-2.5 font-bold"
                          >
                            {isConnecting ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : isActive ? (
                              'Reconnect'
                            ) : (
                              'Connect'
                            )}
                          </Button>
                          <button
                            type="button"
                            onClick={() => handleDelete(row)}
                            disabled={deletingId === row.id}
                            title="Remove camera"
                            className="p-1.5 rounded-lg border border-[var(--border-color)] bg-[var(--bg-card)] text-[var(--text-muted)] hover:text-red-500 hover:border-red-400 transition-all cursor-pointer"
                          >
                            {deletingId === row.id ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Trash2 className="w-3.5 h-3.5" />
                            )}
                          </button>
                        </div>
                      </div>

                      {/* Error Banner for Failed Connection */}
                      {isRowFailed && errorMessage && (
                        <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-[11px] flex items-start gap-1.5 mt-0.5">
                          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-rose-500" />
                          <div className="min-w-0">
                            <span className="font-bold">Connection Failed: </span>
                            <span className="leading-relaxed break-words">{errorMessage}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* ── 3. ADD CAMERA (Compact Expandable Section) ── */}
            {!showAddForm ? (
              <button
                type="button"
                onClick={() => {
                  setErrorMessage(null);
                  setErrorCameraId(null);
                  setShowAddForm(true);
                }}
                className="w-full py-2 rounded-xl border border-dashed border-[var(--border-color)] text-xs font-bold text-[var(--text-secondary)] hover:text-[var(--accent-pond)] hover:border-[var(--accent-pond)] transition-all cursor-pointer flex items-center justify-center gap-1.5"
              >
                <Plus className="w-3.5 h-3.5" /> Add Camera
              </button>
            ) : (
              <div className="p-3 rounded-2xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)] space-y-2.5">
                <div className="flex items-center justify-between pb-1 border-b border-[var(--border-color)]">
                  <span className="text-xs font-bold text-[var(--text-primary)]">New Camera</span>
                  <button
                    type="button"
                    onClick={() => {
                      setShowAddForm(false);
                      setErrorMessage(null);
                      setErrorCameraId(null);
                    }}
                    className="p-1 rounded-md text-[var(--text-muted)] hover:text-[var(--text-primary)] cursor-pointer"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>

                <div>
                  <label className="block text-[10px] font-bold text-[var(--text-secondary)] mb-1">
                    Name
                  </label>
                  <input
                    type="text"
                    placeholder="e.g., Line 1 Inspection Camera"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    className="w-full px-3 py-1.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border-color)] text-xs font-medium text-[var(--text-primary)] focus:outline-hidden focus:border-[var(--accent-pond)]"
                  />
                </div>

                <div className="grid grid-cols-2 gap-1.5 p-1 rounded-xl bg-[var(--bg-card)] border border-[var(--border-color)]">
                  <button
                    type="button"
                    onClick={() => {
                      setNewType('usb');
                      setErrorMessage(null);
                    }}
                    className={`py-1.5 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition-all cursor-pointer ${
                      newType === 'usb'
                        ? 'bg-[var(--accent-pond)] text-white shadow-xs'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    <Usb className="w-3.5 h-3.5" /> USB
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setNewType('ip');
                      setErrorMessage(null);
                    }}
                    className={`py-1.5 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition-all cursor-pointer ${
                      newType === 'ip'
                        ? 'bg-[var(--accent-pond)] text-white shadow-xs'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    <Wifi className="w-3.5 h-3.5" /> IP / PoE
                  </button>
                </div>

                {newType === 'usb' ? (
                  <div className="p-2.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border-color)] flex items-center justify-between gap-2.5">
                    <div className="min-w-0">
                      {detectedUsb ? (
                        <>
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-bold text-[var(--text-primary)] truncate">
                              {detectedUsb.name}
                            </span>
                            <span className="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
                              DETECTED
                            </span>
                          </div>
                          <span className="text-[10px] text-[var(--text-muted)]">Ready to add &amp; connect</span>
                        </>
                      ) : (
                        <span className="text-[10px] text-[var(--text-muted)]">
                          Click Detect to scan for a plugged-in USB OAK camera
                        </span>
                      )}
                    </div>
                    <Button variant="secondary" size="sm" disabled={isScanning} onClick={handleDetectUsb} className="h-7 px-2.5 text-xs">
                      {isScanning ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Detect'}
                    </Button>
                  </div>
                ) : (
                  <input
                    type="text"
                    placeholder="Enter IP address (e.g. 192.168.1.100)"
                    value={newIp}
                    onChange={(e) => setNewIp(e.target.value)}
                    className="w-full px-3 py-1.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border-color)] text-xs font-mono font-medium text-[var(--text-primary)] focus:outline-hidden focus:border-[var(--accent-pond)]"
                  />
                )}

                {/* Form Error Banner */}
                {errorMessage && !errorCameraId && (
                  <div className="p-2 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs flex items-start gap-1.5">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-rose-500" />
                    <div className="min-w-0">
                      <span className="font-bold block">Cannot Connect:</span>
                      <span className="text-[11px] leading-relaxed break-words">{errorMessage}</span>
                    </div>
                  </div>
                )}

                <Button
                  variant="primary"
                  size="sm"
                  disabled={
                    connectingId === 'new' ||
                    (newType === 'usb' && !detectedUsb) ||
                    (newType === 'ip' && !newIp.trim())
                  }
                  onClick={handleAddAndConnect}
                  className="w-full h-8 text-xs font-bold"
                >
                  {connectingId === 'new' ? (
                    <span className="flex items-center justify-center gap-1.5">
                      <Loader2 className="w-3 h-3 animate-spin" /> Connecting...
                    </span>
                  ) : (
                    'Add & Connect'
                  )}
                </Button>
              </div>
            )}
          </div>
        )}

        {activeTab === 'image' && (
          <div className="space-y-3.5">
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

            <div className="flex items-center justify-between pt-1 border-t border-[var(--border-color)]">
              <span className="text-xs font-bold text-[var(--text-primary)]">Continuous Auto-Focus</span>
              <button
                type="button"
                onClick={() => setLocalConfig({ ...localConfig, autoFocus: !localConfig.autoFocus })}
                className={`w-11 h-6 flex items-center rounded-full p-1 transition-colors cursor-pointer ${
                  localConfig.autoFocus ? 'bg-[var(--accent-pond)]' : 'bg-[var(--btn-secondary-border)]'
                }`}
              >
                <span
                  className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${
                    localConfig.autoFocus ? 'translate-x-5' : 'translate-x-0'
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
                <span className="font-mono font-bold text-[var(--accent-pond)]">DepthAI Spatial Detection (YOLOv8)</span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-[var(--border-color)]">
                <span className="text-[var(--text-secondary)]">Hardware Status</span>
                <span className={`font-mono font-bold ${config.connected ? 'text-emerald-500' : 'text-slate-400'}`}>
                  {config.connected ? 'Online • DepthAI Device Connected' : 'Standby • No Device Connected'}
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-[var(--border-color)]">
                <span className="text-[var(--text-secondary)]">Target VPU Latency</span>
                <span className="font-mono font-bold text-[var(--text-primary)]">~6.4 ms / frame (Zero CPU Load)</span>
              </div>
              <div className="flex justify-between py-1.5">
                <span className="text-[var(--text-secondary)]">Configured Profile</span>
                <span className="font-mono font-bold text-[var(--text-primary)]">{localConfig.resolution} @ {localConfig.targetFps} FPS</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
};
