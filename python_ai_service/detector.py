"""
CCTV AI Monitoring Service — Pharmacy Safe Detection Pipeline

Architecture:
  1. Fetches camera list from ASP.NET API
  2. Connects to each camera's RTSP stream
  3. Runs pharmacy YOLO model (detects open_safe, closed_safe, pharmacy_doctor, person)
  4. If an open safe is detected → triggers alert to API (with cooldown)
  5. Draws bounding boxes on frames
  6. Pipes processed frames via FFmpeg to a new RTSP endpoint on MediaMTX

Model classes: closed_safe, pharmacy_doctor, open_safe, person
  - Detection logic is isolated in `detect_and_annotate()` for easy extension.
  - Each camera runs in its own thread (one worker per camera).
"""

import cv2
import subprocess
import threading
import time
import requests
import sys
import logging
from ultralytics import YOLO

# ─── Configuration ────────────────────────────────────────────────────────────
API_BASE_URL = "http://localhost:5000/api"
YOLO_MODEL_PATH = "pharmacy.pt"  # Custom pharmacy model (open_safe, closed_safe, pharmacy_doctor, person)
ALERT_COOLDOWN_SECONDS = 5      # Minimum seconds between alerts per camera
RECONNECT_DELAY_SECONDS = 3     # Delay before reconnecting a dropped stream
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FPS = 10

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─── Load YOLO model once (shared across threads — ultralytics is thread-safe for inference) ─
model = YOLO(YOLO_MODEL_PATH)


def fetch_cameras() -> list[dict]:
    """Fetch the camera list from the ASP.NET API."""
    url = f"{API_BASE_URL}/cameras"
    logger.info(f"Fetching cameras from {url}")
    response = requests.get(url, timeout=10, verify=False)
    response.raise_for_status()
    cameras = response.json()
    logger.info(f"Found {len(cameras)} camera(s)")
    return cameras


def send_alert(camera_id: int, alert_type: str = "MULTIPLE_PEOPLE", severity: str = "Warning"):
    """POST an alert to the ASP.NET API."""
    url = f"{API_BASE_URL}/alerts"
    payload = {
        "cameraId": camera_id,
        "type": alert_type,
        "severity": severity
    }
    try:
        resp = requests.post(url, json=payload, timeout=5, verify=False)
        if resp.ok:
            logger.info(f"Alert sent for camera {camera_id}: {alert_type}")
        else:
            logger.warning(f"Alert API returned {resp.status_code}: {resp.text}")
    except requests.RequestException as e:
        logger.error(f"Failed to send alert: {e}")


def detect_and_annotate(frame, camera_id: int, last_alert_time: dict) -> None:
    """
    Run pharmacy YOLO model on frame, draw bounding boxes, and trigger alerts.

    Detected classes:
      - open_safe    → RED box, triggers SAFE_OPEN alert
      - closed_safe  → GREEN box, safe status (no alert)
      - pharmacy_doctor → BLUE box
      - person       → YELLOW box

    Args:
        frame: OpenCV BGR frame (modified in-place with bounding boxes)
        camera_id: Camera ID for alert context
        last_alert_time: Dict tracking last alert timestamp per camera
    """
    results = model(frame, verbose=False)

    safe_open_detected = False

    # Color map per class: (B, G, R)
    color_map = {
        "open_safe":        (0, 0, 255),     # Red — danger
        "closed_safe":      (0, 255, 0),     # Green — safe
        "pharmacy_doctor":  (255, 128, 0),   # Blue
        "person":           (0, 255, 255),   # Yellow
    }

    for result in results:
        boxes = result.boxes
        for box in boxes:
            cls_id = int(box.cls[0])
            class_name = model.names[cls_id]
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            color = color_map.get(class_name, (255, 255, 255))
            label = f"{class_name} {confidence:.2f}"

            # Draw bounding box and label for every detection
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, label, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # --- Safe open detection ---
            if class_name == "open_safe":
                safe_open_detected = True

    # Display safe status on frame
    if safe_open_detected:
        cv2.putText(frame, "SAFE: OPEN", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
    else:
        cv2.putText(frame, "SAFE: CLOSED", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

    # Trigger alert when safe is open (with cooldown)
    if safe_open_detected:
        now = time.time()
        last = last_alert_time.get(camera_id, 0)
        if now - last >= ALERT_COOLDOWN_SECONDS:
            last_alert_time[camera_id] = now
            threading.Thread(
                target=send_alert,
                args=(camera_id, "SAFE_OPEN", "Critical"),
                daemon=True
            ).start()


def start_ffmpeg_process(processed_rtsp_url: str) -> subprocess.Popen:
    """
    Start an FFmpeg subprocess that reads raw video frames from stdin
    and pushes them as an RTSP stream to MediaMTX.
    """
    cmd = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
        "-r", str(FPS),
        "-i", "-",                          # Read from stdin
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-g", "10",                         # Keyframe every 10 frames (~1 sec at 10fps)
        "-x264-params", "bframes=0:scenecut=0",
        "-b:v", "1500k",                    # Target bitrate
        "-maxrate", "2000k",                # Max bitrate cap
        "-bufsize", "2000k",                # VBV buffer size
        "-pix_fmt", "yuv420p",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        processed_rtsp_url
    ]
    logger.info(f"Starting FFmpeg: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return process


class FrameGrabber:
    """
    Continuously reads frames from an RTSP stream in a dedicated thread,
    always keeping only the latest frame. All VideoCapture operations happen
    inside the reader thread to avoid OpenCV thread-safety issues.
    """
    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url
        self.latest_frame = None
        self.ret = False
        self.lock = threading.Lock()
        self.stopped = False
        self._opened = threading.Event()
        self._open_success = False

        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

        # Wait for the capture to open (or fail) before returning
        self._opened.wait(timeout=10)

    def _reader(self):
        """Create capture and continuously read frames, keeping only the most recent one."""
        cap = cv2.VideoCapture(self.rtsp_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._open_success = cap.isOpened()
        self._opened.set()  # Signal that open attempt is done

        if not self._open_success:
            cap.release()
            return

        while not self.stopped:
            try:
                ret, frame = cap.read()
            except Exception:
                ret, frame = False, None

            with self.lock:
                self.ret = ret
                self.latest_frame = frame

            if not ret:
                break

        cap.release()

    def read(self):
        """Return the latest frame (never stale)."""
        with self.lock:
            return self.ret, self.latest_frame.copy() if self.latest_frame is not None else None

    def is_opened(self):
        return self._open_success

    def release(self):
        self.stopped = True


def process_camera(camera: dict, last_alert_time: dict):
    """
    Worker function for a single camera. Runs in its own thread.
    Connects to RTSP, processes frames, and pipes output to FFmpeg.
    Automatically reconnects if the stream drops.
    """
    camera_id = camera["id"]
    camera_name = camera.get("name", f"Camera-{camera_id}")
    rtsp_url = camera["rtspUrl"]
    processed_rtsp_url = camera["processedRtspUrl"]

    logger.info(f"[{camera_name}] Starting worker — Input: {rtsp_url}, Output: {processed_rtsp_url}")

    while True:
        ffmpeg_proc = None
        grabber = None
        try:
            # Open RTSP stream with frame grabber (always latest frame)
            grabber = FrameGrabber(rtsp_url)
            if not grabber.is_opened():
                logger.warning(f"[{camera_name}] Cannot open RTSP stream. Retrying in {RECONNECT_DELAY_SECONDS}s...")
                grabber.release()
                time.sleep(RECONNECT_DELAY_SECONDS)
                continue

            # Wait briefly for the first frame to arrive
            time.sleep(0.5)

            logger.info(f"[{camera_name}] Connected to RTSP stream")

            # Start FFmpeg output process
            ffmpeg_proc = start_ffmpeg_process(processed_rtsp_url)

            while True:
                ret, frame = grabber.read()
                if not ret or frame is None:
                    logger.warning(f"[{camera_name}] Frame read failed — stream may have dropped")
                    break

                # Resize frame to standard size for consistent FFmpeg piping
                frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))

                # Run detection and annotation (modifies frame in-place)
                detect_and_annotate(frame, camera_id, last_alert_time)

                # Pipe annotated frame to FFmpeg
                try:
                    ffmpeg_proc.stdin.write(frame.tobytes())
                except BrokenPipeError:
                    logger.warning(f"[{camera_name}] FFmpeg pipe broken")
                    break

        except Exception as e:
            logger.error(f"[{camera_name}] Error: {e}")

        finally:
            # Cleanup before reconnect
            if grabber is not None:
                grabber.release()
            if ffmpeg_proc is not None:
                try:
                    ffmpeg_proc.stdin.close()
                except Exception:
                    pass
                ffmpeg_proc.terminate()

        logger.info(f"[{camera_name}] Reconnecting in {RECONNECT_DELAY_SECONDS}s...")
        time.sleep(RECONNECT_DELAY_SECONDS)


def main():
    """Entry point: fetch cameras and spawn one worker thread per camera."""
    # Wait for the API to be ready
    logger.info("Waiting for API to be available...")
    while True:
        try:
            cameras = fetch_cameras()
            break
        except Exception as e:
            logger.warning(f"API not ready: {e}. Retrying in 3s...")
            time.sleep(3)

    if not cameras:
        logger.error("No cameras found in the database. Exiting.")
        sys.exit(1)

    # Shared alert cooldown tracker (thread-safe via GIL for dict operations)
    last_alert_time: dict[int, float] = {}

    # Spawn one worker thread per camera
    threads = []
    for camera in cameras:
        t = threading.Thread(
            target=process_camera,
            args=(camera, last_alert_time),
            name=f"Camera-{camera['id']}",
            daemon=True
        )
        t.start()
        threads.append(t)
        logger.info(f"Started worker thread for camera {camera['id']} ({camera.get('name', 'unknown')})")

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
