"""
CCTV AI Monitoring Service — Human Detection Pipeline

Architecture:
  1. Fetches camera list from ASP.NET API
  2. Connects to each camera's RTSP stream
  3. Runs YOLOv8 model to detect persons
  4. If a person is detected → triggers alert to API (with cooldown)
  5. Draws bounding boxes on frames
  6. Pipes processed frames via FFmpeg to a new RTSP endpoint on MediaMTX

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
YOLO_MODEL_PATH = "yolov8s.pt"   # Standard YOLOv8 model — detects persons
ALERT_COOLDOWN_SECONDS = 5      # Minimum seconds between alerts per camera
RECONNECT_DELAY_SECONDS = 3     # Delay before reconnecting a dropped stream
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FPS = 10
RTSP_CONNECTION_TIMEOUT = 10    # Timeout for RTSP connection (seconds)
RTSP_READ_TIMEOUT = 5           # Timeout for frame reads (seconds)

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
    Run YOLOv8 on frame, draw bounding boxes around persons, and trigger alerts.

    Args:
        frame: OpenCV BGR frame (modified in-place with bounding boxes)
        camera_id: Camera ID for alert context
        last_alert_time: Dict tracking last alert timestamp per camera
    """
    results = model(frame, verbose=False, classes=[0])  # class 0 = person in COCO

    person_count = 0
    PERSON_COLOR = (0, 255, 255)  # Yellow

    for result in results:
        boxes = result.boxes
        for box in boxes:
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            label = f"person {confidence:.2f}"
            cv2.rectangle(frame, (x1, y1), (x2, y2), PERSON_COLOR, 2)
            cv2.putText(frame, label, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, PERSON_COLOR, 2)

            person_count += 1

    # Display detected human count on frame
    cv2.putText(frame, f"HUMANS: {person_count}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

    # Trigger alert when one or more humans are detected (with cooldown)
    if person_count > 0:
        now = time.time()
        last = last_alert_time.get(camera_id, 0)
        if now - last >= ALERT_COOLDOWN_SECONDS:
            last_alert_time[camera_id] = now
            threading.Thread(
                target=send_alert,
                args=(camera_id, f"HUMAN_COUNT_DETECTED_{person_count}", "Warning"),
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
        "-hide_banner",
        "-loglevel", "error",               # Suppress AU header warnings
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
        "-r", str(FPS),
        "-i", "-",                          # Read from stdin
        "-c:v", "libx264",
        "-preset", "ultrafast",             # Faster than veryfast for lower latency
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-g", "10",                         # Keyframe every 10 frames (~1 sec at 10fps)
        "-x264-params", "bframes=0:scenecut=0:nal-hrd=cbr",
        "-b:v", "1000k",                    # Reduced bitrate for faster encoding
        "-maxrate", "1200k",                # Max bitrate cap
        "-bufsize", "1200k",                # Smaller VBV buffer for lower latency
        "-pix_fmt", "yuv420p",
        "-an",                              # Disable audio completely (prevents AU header errors)
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        "-flvflags", "no_duration_filesize",
        processed_rtsp_url
    ]
    logger.info(f"Starting FFmpeg stream output")
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,             # Capture stderr to avoid filling up
        bufsize=0                           # Unbuffered stdin for immediate writing
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
        
        # Aggressive buffer and timeout settings to minimize latency
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)  # Disable autofocus for faster capture
        
        # Set receive timeout (milliseconds) — helps detect dead connections faster
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_CONNECTION_TIMEOUT * 1000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, RTSP_READ_TIMEOUT * 1000)

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
    
    # Add TCP transport and timeout parameters to RTSP URL for reliability
    rtsp_url_optimized = f"{rtsp_url}?tcp" if "?" not in rtsp_url else f"{rtsp_url}&tcp"

    logger.info(f"[{camera_name}] Starting worker — Input: {rtsp_url}, Output: {processed_rtsp_url}")

    retry_count = 0
    max_retries = 5

    while True:
        ffmpeg_proc = None
        grabber = None
        try:
            # Open RTSP stream with frame grabber (always latest frame)
            grabber = FrameGrabber(rtsp_url_optimized)
            if not grabber.is_opened():
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"[{camera_name}] Max retries ({max_retries}) exceeded. Waiting longer before retry...")
                    time.sleep(RECONNECT_DELAY_SECONDS * 3)
                    retry_count = 0
                else:
                    logger.warning(f"[{camera_name}] Cannot open RTSP stream. Retrying in {RECONNECT_DELAY_SECONDS}s... (attempt {retry_count})")
                    time.sleep(RECONNECT_DELAY_SECONDS)
                continue

            retry_count = 0  # Reset on successful connection
            logger.info(f"[{camera_name}] Connected to RTSP stream")

            # Wait briefly for the first frame to arrive
            time.sleep(0.3)

            # Start FFmpeg output process
            ffmpeg_proc = start_ffmpeg_process(processed_rtsp_url)

            frame_count = 0
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
                    frame_count += 1
                except (BrokenPipeError, OSError):
                    logger.warning(f"[{camera_name}] FFmpeg pipe broken or disconnected")
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
                try:
                    ffmpeg_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    ffmpeg_proc.kill()

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
