"use client";

import { useState } from "react";

import {
  CameraControlKey,
  useCameraSystemStore,
} from "../../store/useCameraSystemStore";

import {
  cameraService,
} from "../service/cameraService";

import {
  showToast,
} from "../../lib/toast";



/* ====================================================== */
/* TYPES */
/* ====================================================== */

export type SliderItem = {
  label: string;
  type: CameraControlKey;
  min: number;
  max: number;
  step?: number;
};

/* ====================================================== */
/* HOOK */
/* ====================================================== */

export function useStreamingControls() {
  /* ====================================================== */
  /* STORE */
  /* ====================================================== */

  const camera =
    useCameraSystemStore(
      (s) => s.camera
    );

  const setControl =
    useCameraSystemStore(
      (s) => s.setControl
    );

  const exposure =
    useCameraSystemStore(
      (s) => s.exposure
    );

  const gain =
    useCameraSystemStore(
      (s) => s.gain
    );

  const focus =
    useCameraSystemStore(
      (s) => s.focus
    );

  const brightness =
    useCameraSystemStore(
      (s) => s.brightness
    );

  const contrast =
    useCameraSystemStore(
      (s) => s.contrast
    );

  /* ====================================================== */
  /* STATE */
  /* ====================================================== */

  const [saving, setSaving] =
    useState(false);

  /* ====================================================== */
  /* SETTINGS */
  /* ====================================================== */

  const settings: SliderItem[] =
    [
      {
        label:
          "Exposure",

        type:
          "exposure",

        min: 0,
        max: 100000,
        step: 10,
      },

      {
        label:
          "Gain",

        type:
          "gain",

        min: 0,
        max: 1000,
        step: 1,
      },

      {
        label:
          "Focus",

        type:
          "focus",

        min: 0,
        max: 255,
        step: 1,
      },

      {
        label:
          "Brightness",

        type:
          "brightness",

        min: 0,
        max: 100,
        step: 1,
      },

      {
        label:
          "Contrast",

        type:
          "contrast",

        min: 0,
        max: 100,
        step: 1,
      },
    ];

  /* ====================================================== */
  /* SAVE */
  /* ====================================================== */

  const handleSave =
    async () => {
      if (
        !camera?.id ||
        saving
      ) {
        return;
      }

      setSaving(true);


      try {
        const store =
          useCameraSystemStore.getState();

        await cameraService.updateCamera(
          String(
            camera.id
          ),
          {
            control_mode:
              "manual",

            auto_focus:
              false,

            auto_exposure:
              false,

            exposure:
              Number(
                store.exposure
              ),

            gain:
              Number(
                store.gain
              ),

            focus:
              Number(
                store.focus
              ),

            brightness:
              Math.min(
                100,
                Math.max(
                  0,
                  Number(
                    store.brightness
                  )
                )
              ),

            contrast:
              Math.min(
                100,
                Math.max(
                  0,
                  Number(
                    store.contrast
                  )
                )
              ),

            name:
              camera.name,

            ip_address:
              camera.ip ??
              (camera as any)
                .ip_address,

            resolution:
              camera.resolution,

            rotation_angle:
              camera.rotation_angle,

            is_enabled:
              camera.is_enabled,
          }
        );

        useCameraSystemStore.setState(
          {
            camera: {
              ...camera,

              exposure:
                store.exposure,

              gain:
                store.gain,

              focus:
                store.focus,

              brightness:
                store.brightness,

              contrast:
                store.contrast,
            },
          }
        );

        showToast(
          "success",
          "Camera settings saved"
        );
      } catch (err) {
        console.error(
          err
        );

        showToast(
          "error",
          "Failed to save camera settings"
        );
      } finally {
        setSaving(false);

      }
    };

  /* ====================================================== */
  /* APPLY */
  /* ====================================================== */

  const applyChange = (
    type: CameraControlKey,
    min: number,
    max: number,
    newValue: number
  ) => {
    const clamped =
      Math.min(
        max,
        Math.max(
          min,
          newValue
        )
      );

    setControl(
      type,
      clamped
    );

  };

  /* ====================================================== */
  /* KEYBOARD */
  /* ====================================================== */

  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement>,
    type: CameraControlKey,
    value: number,
    min: number,
    max: number,
    step: number
  ) => {
    let delta = 0;

    if (
      e.key ===
        "ArrowUp" ||
      e.key ===
        "ArrowRight"
    ) {
      delta = step;
    }

    if (
      e.key ===
        "ArrowDown" ||
      e.key ===
        "ArrowLeft"
    ) {
      delta = -step;
    }

    if (delta === 0)
      return;

    e.preventDefault();

    /* ================= SHIFT ================= */

    if (
      e.shiftKey
    ) {
      delta *= 10;
    }

    /* ================= ALT ================= */

    if (
      e.altKey
    ) {
      delta *= 0.2;
    }

    applyChange(
      type,
      min,
      max,
      Number(value) +
        delta
    );
  };

  /* ====================================================== */
  /* WHEEL */
  /* ====================================================== */

  const handleWheel = (
    e: React.WheelEvent<HTMLInputElement>,
    type: CameraControlKey,
    value: number,
    min: number,
    max: number,
    step: number
  ) => {
    e.preventDefault();

    let delta =
      e.deltaY < 0
        ? step
        : -step;

    if (
      e.shiftKey
    ) {
      delta *= 10;
    }

    if (
      e.altKey
    ) {
      delta *= 0.2;
    }

    applyChange(
      type,
      min,
      max,
      Number(value) +
        delta
    );
  };

  return {
    saving,
    settings,

    exposure,
    gain,
    focus,
    brightness,
    contrast,

    handleSave,
    handleKeyDown,
    handleWheel,
    applyChange,
  };
}