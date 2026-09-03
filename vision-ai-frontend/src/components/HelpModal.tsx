import React from 'react';
import { BookOpen, AlertTriangle, Sparkles } from 'lucide-react';
import { playWaterDropSound } from '../utils/audio';
import { Modal, Button } from './ui';

interface HelpModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const HelpModal: React.FC<HelpModalProps> = ({ isOpen, onClose }) => {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      maxWidth="xl"
      icon={<BookOpen className="w-4 h-4 text-[var(--accent-pond)]" />}
      title="Vision Monitor • User Guide"
      description="Computer Vision Object Counting & Anomaly Detection"
      footer={
        <Button
          variant="primary"
          size="md"
          onClick={() => {
            playWaterDropSound();
            onClose();
          }}
        >
          Got it!
        </Button>
      }
    >
      <div className="space-y-4 text-xs leading-relaxed text-[var(--text-primary)]">
        
        {/* Section 1: How Detection & Anomaly Rules Work */}
        <div className="p-3.5 rounded-2xl bg-[var(--status-warn-bg)] border border-[var(--status-warn-border)]">
          <h3 className="font-bold text-sm text-[var(--status-warn-text)] mb-1.5 flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4 text-[var(--status-warn-text)]" />
            Anomaly Detection Logic
          </h3>
          <ul className="space-y-1.5 text-[var(--text-secondary)]">
            <li>• <strong className="text-[var(--text-primary)]">Detected = Expected:</strong> Status is <span className="text-[var(--status-normal-text)] font-bold">NORMAL ✓</span></li>
            <li>• <strong className="text-[var(--text-primary)]">Detected &gt; Expected:</strong> Triggers <span className="text-[var(--status-anomaly-text)] font-bold">ANOMALY (+Count Drift)</span></li>
            <li>• <strong className="text-[var(--text-primary)]">Detected &lt; Expected:</strong> Triggers <span className="text-[var(--status-anomaly-text)] font-bold">ANOMALY (Missing Ducks)</span></li>
            <li>• <strong className="text-[var(--text-primary)]">Foreign Species:</strong> (Unknown, Heron, Predator) triggers instant <span className="text-[var(--status-anomaly-text)] font-bold">ANOMALY Alert</span>.</li>
          </ul>
        </div>

        {/* Section 2: Vision Monitor Capabilities */}
        <div>
          <h3 className="font-bold text-sm text-[var(--text-primary)] mb-2 flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-[var(--accent-duck)]" />
            Vision Capabilities
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
            <div className="p-2.5 rounded-xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)]">
              <span className="font-bold text-[var(--accent-pond)]">RAW vs INFERENCE</span>
              <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                Toggle between raw camera feed and YOLOv8 bounding box overlay with real-time confidence scores.
              </p>
            </div>

            <div className="p-2.5 rounded-xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)]">
              <span className="font-bold text-[var(--accent-pond)]">Video & Camera Stream</span>
              <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                Upload surveillance MP4/WebM drone video or connect directly to an OAK camera stream.
              </p>
            </div>
          </div>
        </div>

        {/* Section 3: Keyboard Shortcuts */}
        <div>
          <h3 className="font-bold text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
            Keyboard Shortcuts
          </h3>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            <div className="flex justify-between p-2 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)]">
              <span>Toggle Inference:</span>
              <kbd className="px-1.5 py-0.5 bg-[var(--bg-card)] text-[var(--text-primary)] border border-[var(--border-color)] rounded-md font-bold shadow-xs">I</kbd>
            </div>
            <div className="flex justify-between p-2 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)]">
              <span>Start / Pause:</span>
              <kbd className="px-1.5 py-0.5 bg-[var(--bg-card)] text-[var(--text-primary)] border border-[var(--border-color)] rounded-md font-bold shadow-xs">Space</kbd>
            </div>
            <div className="flex justify-between p-2 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)]">
              <span>Toggle Fullscreen:</span>
              <kbd className="px-1.5 py-0.5 bg-[var(--bg-card)] text-[var(--text-primary)] border border-[var(--border-color)] rounded-md font-bold shadow-xs">F</kbd>
            </div>
            <div className="flex justify-between p-2 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)]">
              <span>Snapshot:</span>
              <kbd className="px-1.5 py-0.5 bg-[var(--bg-card)] text-[var(--text-primary)] border border-[var(--border-color)] rounded-md font-bold shadow-xs">S</kbd>
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
};
