"""
CCTV AI Monitoring Service — Security Zone Detection Pipeline

Architecture:
  1. Fetches camera list from ASP.NET API
  2. Connects to each camera's RTSP stream
  3. Runs YOLOv8s COCO model counting persons (class 0) inside a security zone
  4. ≥1 person in zone  → "Security Present" (green banner)
  5. 0 people in zone   → "SECURITY NOT PRESENT" (red banner + API alert with cooldown)
  6. Draws the security zone in yellow, detected person boxes in cyan
  7. Pipes annotated frames via FFmpeg to a new RTSP endpoint on MediaMTX

Zone coordinates (native camera resolution): (69, 138) → (1529, 730)

The backend AlertDelayService holds the alert in a buffer for the configured
SecuritySignalRDelaySeconds before forwarding it to the frontend via SignalR.
This script only handles detection and posting to the REST API.
"""

import cv2
import subprocess
import threading
import time
import requests
import sys
import logging
from ultralytics import YOLO

# ─── Configuration ─────────────────────────────────────────────────────────────
API_BASE_URL            = "http://localhost:5000/api"
YOLO_MODEL_PATH         = "yolov8s.pt"     # COCO weights — person is class 0
ALERT_COOLDOWN_SECONDS  = 5                # Min seconds between alert POSTs per camera
RECONNECT_DELAY_SECONDS = 3
FRAME_WIDTH             = 640
FRAME_HEIGHT            = 480
FPS                     = 10
RTSP_CONNECTION_TIMEOUT = 10
RTSP_READ_TIMEOUT       = 5

# ─── Security zone in native camera resolution ─────────────────────────────────
ZONE_X1, ZONE_Y1 = 69,   138
ZONE_X2, ZONE_Y2 = 1529, 730

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─── Load YOLO model once (shared across threads) ──────────────────────────────
model = YOLO(YOLO_MODEL_PATH)


# ───────────────────────────────────────────────────────────────────────────────
def fetch_cameras() -> list[dict]:
    """Fetch the camera list from the ASP.NET API."""
    url = f"{API_BASE_URL}/cameras"
    logger.info(f"Fetching cameras from {url}")
    response = requests.get(url, timeout=10, verify=False)
    response.raise_for_status()
    cameras = response.json()
    logger.info(f"Found {len(cameras)} camera(s)")
    return cameras


def send_alert(camera_id: int, alert_type: str = "SECURITY_NOT_PRESENT", severity: str = "Critical"):
    """POST an alert to the ASP.NET API."""
    url = f"{API_BASE_URL}/alerts"
    payload = {"cameraId": camera_id, "type": alert_type, "severity": severity}
    try:
        resp = requests.post(url, json=payload, timeout=5, verify=False)
        if resp.ok:
            logger.info(f"Alert sent for camera {camera_id}: {alert_type}")
        else:
            logger.warning(f"Alert API returned {resp.status_code}: {resp.text}")
    except requests.RequestException as e:
        logger.error(f"Failed to send alert: {e}")


def _center_in_zone(x1: int, y1: int, x2: int, y2: int) -> bool:
    """Return True if the bounding-box center falls inside the security zone."""
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    return ZONE_X1 <= cx <= ZONE_X2 and ZONE_Y1 <= cy <= ZONE_Y2


def detect_and_annotate(frame, camera_id: int, last_alert_time: dict) -> None:
    """
    Run YOLOv8s on the native-resolution frame.

    • Draws the security zone rectangle in yellow.
    • Counts persons (class 0) whose bounding-box center is inside the zone.
    • ≥1 person → green "Security Present" banner.
    •  0 people → red "SECURITY NOT PRESENT" banner + API alert (cooldown-gated).

    The frame is annotated in-place; caller resizes it for FFmpeg output.
    """
    ZONE_COLOR   = (0, 255, 255)   # Yellow
    PERSON_COLOR = (255, 255, 0)   # Cyan
    OK_COLOR     = (0, 200, 0)     # Green
    ALERT_COLOR  = (0, 0, 255)     # Red

    # Draw security zone
    cv2.rectangle(frame, (ZONE_X1, ZONE_Y1), (ZONE_X2, ZONE_Y2), ZONE_COLOR, 2)
    cv2.putText(frame, "Security Zone", (ZONE_X1 + 4, ZONE_Y1 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, ZONE_COLOR, 2)

    # Run inference — only detect class 0 (person)
    results = model(frame, verbose=False, classes=[0])

    person_count = 0
    for result in results:
        for box in result.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            conf = float(box.conf[0])

            # Draw every detected person (dim box if outside zone)
            in_zone = _center_in_zone(x1, y1, x2, y2)
            color = PERSON_COLOR if in_zone else (100, 100, 100)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"person {conf:.2f}", (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

            if in_zone:
                person_count += 1

    # Status banner
    if person_count >= 1:
        banner = f"Security Present  ({person_count})"
        cv2.rectangle(frame, (0, 0), (500, 44), OK_COLOR, -1)
        cv2.putText(frame, banner, (8, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    else:
        banner = "SECURITY NOT PRESENT"
        cv2.rectangle(frame, (0, 0), (560, 44), ALERT_COLOR, -1)
        cv2.putText(frame, banner, (8, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        logger.warning(f"[Camera {camera_id}] SECURITY NOT PRESENT in zone")

        # Send alert to API (cooldown-gated)
        now = time.time()
        if now - last_alert_time.get(camera_id, 0) >= ALERT_COOLDOWN_SECONDS:
            last_alert_time[camera_id] = now
            threading.Thread(
                target=send_alert,
                args=(camera_id, "SECURITY_NOT_PRESENT", "Critical"),
                daemon=True
            ).start()


# ─── FFmpeg output process ─────────────────────────────────────────────────────
def start_ffmpeg_process(processed_rtsp_url: str) -> subprocess.Popen:
    cmd = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
        "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-g", "10",
        "-x264-params", "bframes=0:scenecut=0:nal-hrd=cbr",
        "-b:v", "1000k",
        "-maxrate", "1200k",
        "-bufsize", "1200k",
        "-pix_fmt", "yuv420p",
        "-an",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        "-flvflags", "no_duration_filesize",
        processed_rtsp_url
    ]
    logger.info("Starting FFmpeg stream output")
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=0
    )


# ─── Frame grabber (latest-frame-only, non-blocking) ──────────────────────────
class FrameGrabber:
    """
    Reads frames from an RTSP stream in a dedicated thread, always keeping
    only the most recent frame so the processing loop never falls behind.
    """

    def __init__(self, rtsp_url: str):
        self.rtsp_url    = rtsp_url
        self.latest_frame = None
        self.ret          = False
        self.lock         = threading.Lock()
        self.stopped      = False
        self._opened      = threading.Event()
        self._open_success = False

        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        self._opened.wait(timeout=RTSP_CONNECTION_TIMEOUT)

    def _reader(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_CONNECTION_TIMEOUT * 1000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC,  RTSP_READ_TIMEOUT  * 1000)

        self._open_success = cap.isOpened()
        self._opened.set()

        if not self._open_success:
            cap.release()
            return

        while not self.stopped:
            try:
                ret, frame = cap.read()
            except Exception:
                ret, frame = False, None

            with self.lock:
                self.ret          = ret
                self.latest_frame = frame

            if not ret:
                break

        cap.release()

    def read(self):
        with self.lock:
            return self.ret, (self.latest_frame.copy() if self.latest_frame is not None else None)

    def is_opened(self):
        return self._open_success

    def release(self):
        self.stopped = True


# ─── Per-camera worker ─────────────────────────────────────────────────────────
def process_camera(camera: dict, last_alert_time: dict):
    """
    Runs in its own thread. Connects to RTSP, detects security presence every
    frame, annotates, and pipes to FFmpeg. Reconnects automatically on failure.
    """
    camera_id   = camera["id"]
    camera_name = camera.get("name", f"Camera-{camera_id}")
    rtsp_url    = camera["rtspUrl"]
    out_url     = camera["processedRtspUrl"]

    rtsp_url_opt = f"{rtsp_url}?tcp" if "?" not in rtsp_url else f"{rtsp_url}&tcp"
    logger.info(f"[{camera_name}] Starting — Input: {rtsp_url}, Output: {out_url}")

    retry_count = 0
    max_retries = 5

    while True:
        ffmpeg_proc = None
        grabber     = None
        try:
            grabber = FrameGrabber(rtsp_url_opt)
            if not grabber.is_opened():
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"[{camera_name}] Max retries exceeded. Waiting before retry...")
                    time.sleep(RECONNECT_DELAY_SECONDS * 3)
                    retry_count = 0
                else:
                    logger.warning(f"[{camera_name}] Cannot open stream. Retry {retry_count}/{max_retries}...")
                    time.sleep(RECONNECT_DELAY_SECONDS)
                continue

            retry_count = 0
            logger.info(f"[{camera_name}] Connected")
            time.sleep(0.3)

            ffmpeg_proc = start_ffmpeg_process(out_url)

            while True:
                ret, frame = grabber.read()
                if not ret or frame is None:
                    logger.warning(f"[{camera_name}] Frame read failed — stream may have dropped")
                    break

                # Detect on native resolution so zone coordinates are accurate
                detect_and_annotate(frame, camera_id, last_alert_time)

                # Resize annotated frame for FFmpeg output
                frame_out = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
                try:
                    ffmpeg_proc.stdin.write(frame_out.tobytes())
                except (BrokenPipeError, OSError):
                    logger.warning(f"[{camera_name}] FFmpeg pipe broken")
                    break

        except Exception as e:
            logger.error(f"[{camera_name}] Error: {e}")

        finally:
            if grabber is not None:
                grabber.release()
            if ffmpeg_proc is not None:
                try:
                    ffmpeg_proc.stdin.close()
                except Exception:
                    pass
                ffmpeg_proc.terminate()
                try:
                    ffmpeg_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    ffmpeg_proc.kill()

        logger.info(f"[{camera_name}] Reconnecting in {RECONNECT_DELAY_SECONDS}s...")
        time.sleep(RECONNECT_DELAY_SECONDS)


# ─── Entry point ───────────────────────────────────────────────────────────────
def main():
    logger.info("Security detector starting — waiting for API...")
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

    last_alert_time: dict[int, float] = {}

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
        logger.info(f"Started worker for camera {camera['id']} ({camera.get('name', 'unknown')})")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
