"""TrayCount example: a video file, or camera frames one at a time.
    python example.py path/to/video.mp4 [expected_count] -> shows live preview, prints counts, writes overlay.mp4
"""
import os
import sys
import cv2
from traycount import TrayCounter


def main():
    if len(sys.argv) < 2:
        print("Usage: python example.py <path_to_video_or_camera_id> [expected_duck_count]")
        print("Example: python example.py \"C:\\Users\\EmageVision\\Videos\\duck_videos\\recorded_camera_1788871269188_30fps.mp4\" 18")
        sys.exit(1)

    video_arg = sys.argv[1]
    source = int(video_arg) if video_arg.isdigit() else video_arg

    expected = None
    if len(sys.argv) > 2:
        if sys.argv[2] in ("--expected", "-e") and len(sys.argv) > 3:
            expected = int(sys.argv[3])
        elif sys.argv[2].startswith("--expected="):
            expected = int(sys.argv[2].split("=")[1])
        else:
            try:
                expected = int(sys.argv[2])
            except ValueError:
                expected = None

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.yaml")

    tc = TrayCounter(config_path)  # paths inside config.yaml are relative to config.yaml
    print("Loading models onto GPU...")
    tc.load_model()
    print("Models loaded successfully.")

    # --- camera / video: one frame at a time ---
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"Error: Could not open video source: {source}")
        tc.unload_model()
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print(f"Starting session with fps={fps:.2f}, expected_duck_count={expected}")
    tc.reset(fps=fps, expected_duck_count=expected)

    window_name = "TrayCount 1.2.0 - Live Preview (Press 'q' to quit)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1280, 720)

    out = None
    output_video_path = os.path.join(script_dir, "overlay.mp4")

    print("\nRunning inference... (Press 'q' or 'ESC' in the video window to quit)\n")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            r = tc.process_frame(frame)  # results: counts, paused, toys (id, kind, outline), tray

            # Print every frame
            mask_anom_str = ""
            if r.get("mask_anomalies"):
                mask_details = [
                    f"false_id={a['ids']} on_id={a['other_id']} box={a['box']}"
                    for a in r["mask_anomalies"]
                ]
                mask_anom_str = f" | FalseMasks: [{', '.join(mask_details)}]"

            thumbs_str = f" | Thumbs: {list(r['thumbnails'].keys())}" if r.get("thumbnails") else ""

            print(
                f"Frame {r['frame_index']:4d} | Status: {r['status']:7s} | Reasons: {r['reasons']} | "
                f"Expected: {r['expected_duck_count']} | Ducks: {r['counts']['ducks'] if r['counts'] else '-'} | "
                f"Missing: {r['missing_ids']} | Excess: {r['excess_ids']} | Other: {r['other_ids']}"
                f"{mask_anom_str}{thumbs_str} | {r['latency_ms']['total']} ms ({r['fps']} FPS)"
            )

            pic = tc.draw(frame)  # overlay picture

            # Show live preview in window
            cv2.imshow(window_name, pic)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:
                print("\nPreview stopped by user.")
                break

            if out is None:
                out = cv2.VideoWriter(
                    output_video_path,
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    pic.shape[1::-1],
                )
            out.write(pic)
    finally:
        cap.release()
        if out is not None:
            out.release()
            print(f"\nOverlay video saved to: {output_video_path}")
        cv2.destroyAllWindows()

    # templates: a picture of every toy
    print("\nTemplates collected:")
    for i, tp in tc.templates().items():
        print(f"  Toy #{i:02d} ({tp['kind']}) -> {tp.get('path', 'in-memory')}")

    tc.unload_model()
    print("Done. GPU memory freed.")


if __name__ == "__main__":
    main()

