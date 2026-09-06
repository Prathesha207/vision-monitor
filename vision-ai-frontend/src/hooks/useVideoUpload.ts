import { useState, useEffect, useRef } from 'react';
import { getApiBaseUrl } from '../lib/api';
import { playWaterDropSound } from '../utils/audio';

export const useVideoUpload = (
  fileInputRef: React.RefObject<HTMLInputElement | null>,
  expectedDucks: number,
  onVideoUploaded?: (videoUrl: string, fileName: string, sessionId?: string) => void,
  recordedFile?: File | null,
  clearRecording?: () => void,
  initialUploadFile?: File
) => {
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [isSelectingVideo, setIsSelectingVideo] = useState(false);
  
  const blobUrlRef = useRef<string | null>(null);

  const processUploadedFile = async (file: File) => {
    playWaterDropSound();
    
    setUploadProgress(0);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('expected_ducks', expectedDucks.toString());

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${getApiBaseUrl()}/video/upload`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        setUploadProgress(Math.min(99, percent));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          setUploadProgress(100);
          setTimeout(async () => {
            setUploadProgress(null);
            if (onVideoUploaded) {
              const rawVideoUrl = `${getApiBaseUrl()}/video/raw/${data.session_id}`;
              try {
                await fetch(`${getApiBaseUrl()}/video/update_expected/${data.session_id}`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ count: expectedDucks })
                }).catch(() => {});
              } catch {}
              onVideoUploaded(rawVideoUrl, file.name, data.session_id);
            }
          }, 350);
        } catch (e) {
          setUploadProgress(null);
        }
      } else {
        setUploadProgress(null);
        console.error('Upload failed with status:', xhr.status);
      }
    };

    xhr.onerror = () => {
      setUploadProgress(null);
      console.error('Network error during video upload');
    };

    xhr.send(formData);
  };

  useEffect(() => {
    if (recordedFile) {
      processUploadedFile(recordedFile);
      if (clearRecording) {
        clearRecording(); // Ensure it is consumed
      }
    }
  }, [recordedFile]);

  useEffect(() => {
    if (initialUploadFile) {
      processUploadedFile(initialUploadFile);
    }
  }, [initialUploadFile]);

  useEffect(() => {
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
      }
    };
  }, []);

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      processUploadedFile(file);
    }
  };

  const handleSelectVideoAndStart = async () => {
    const electronApi = (window as any).electronAPI;
    if (electronApi && typeof electronApi.selectFile === 'function') {
      try {
        setIsSelectingVideo(true);
        const filePath = await electronApi.selectFile();
        if (!filePath) {
          setIsSelectingVideo(false);
          return;
        }

        const baseUrl = getApiBaseUrl();
        const res = await fetch(`${baseUrl}/video/inference/path`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            video_path: filePath,
            expected_ducks: expectedDucks,
          }),
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.message || 'Failed to start path inference');
        }

        const data = await res.json();
        const filename = data.video_name || filePath.split(/[/\\]/).pop() || 'video.mp4';

        // Automatically start inference on selected video
        try {
          await fetch(`${baseUrl}/video/update_expected/${data.session_id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ count: expectedDucks })
          }).catch(() => {});
          await fetch(`${baseUrl}/video/start/${data.session_id}`, { method: 'POST' });
        } catch (e) {
          console.error('Failed to start inference on desktop upload:', e);
        }

        if (onVideoUploaded) {
          const streamUrl = `${baseUrl}/video/stream/${data.session_id}`;
          onVideoUploaded(streamUrl, filename, data.session_id);
        }
      } catch (err: any) {
        console.error('Desktop video selection error:', err);
      } finally {
        setIsSelectingVideo(false);
      }
      return;
    }

    fileInputRef.current?.click();
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent, isVideoSource: boolean) => {
    e.preventDefault();
    setIsDragOver(false);
    if (!isVideoSource) return;
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith('video/')) {
      processUploadedFile(file);
    }
  };

  return {
    isDragOver,
    uploadProgress,
    isSelectingVideo,
    processUploadedFile,
    handleFileInputChange,
    handleSelectVideoAndStart,
    handleDragOver,
    handleDragLeave,
    handleDrop
  };
};
