# CCTV AI Monitoring System — Run Instructions

## Architecture

```
MP4 → FFmpeg → RTSP (/mystream) → MediaMTX
     → Python AI (YOLO) → Bounding Boxes → FFmpeg → RTSP (/processed) → MediaMTX → WebRTC
     → Alerts → ASP.NET API → DB + SignalR → Angular Dashboard
```

---

## Prerequisites

- **SQL Server** running locally (or update connection string in `appsettings.json`)
- **.NET 8 SDK**
- **Python 3.11+** with pip
- **FFmpeg** on PATH
- **MediaMTX** (download from https://github.com/bluenviron/mediamtx/releases)
- **Node.js 18+** with npm

---

## Step 1: Start MediaMTX

Download and extract MediaMTX, then run:

```bash
./mediamtx
```

Default ports:
- RTSP: `8554`
- WebRTC: `8889`

---

## Step 2: Simulate an RTSP Camera (using an MP4 file)

Push a video file to MediaMTX as an RTSP stream:

```bash
C:\ffmpeg\bin\ffmpeg -re -stream_loop -1 -i your_video.mp4 -c:v libx264 -preset veryfast -tune zerolatency -f rtsp rtsp://localhost:8554/mystream
```

This creates the stream at `rtsp://localhost:8554/mystream`.

---

## Step 3: Run the ASP.NET API

```bash
cd CameraBE
dotnet run --launch-profile http
```


The API starts on `http://localhost:5000`.

Endpoints:
- `GET  http://localhost:5000/api/cameras`
- `GET  http://localhost:5000/api/alerts`
- `POST http://localhost:5000/api/alerts`
- `WS   http://localhost:5000/alertHub` (SignalR)

The database is auto-migrated and seeded on startup.

---

## Step 4: Run the Python AI Service

```bash
cd python_ai_service

# Create virtual environment (optional but recommended)
python -m venv venv
venv\Scripts\activate    # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run the detector
python detector.py
```

The Python service will:
1. Fetch cameras from the API
2. Connect to each camera's RTSP stream
3. Run YOLO person detection
4. Draw bounding boxes
5. Push processed frames to `python detector.py`
6. POST alerts when person count > 1

---

## Step 5: Run the Angular Frontend

```bash
cd camera-frontend
npm install
ng serve
```

Open `http://localhost:4200` to see:
- Live processed video stream (WebRTC via MediaMTX)
- Real-time alert notifications via SignalR
- Alerts table

---

## Testing the Full Flow

1. Start MediaMTX
2. Push a test video with `ffmpeg` (Step 2)
3. Start the API (`dotnet run`)
4. Start the Python detector (`python detector.py`)
5. Open the Angular app at `http://localhost:4200`

When the Python service detects > 1 person:
- An alert is POSTed to the API
- The API saves it to the database
- SignalR broadcasts it to the Angular frontend
- A toast notification appears + the alerts table updates

The processed stream (with bounding boxes) is visible at:
- WebRTC: `http://localhost:8889/processed/`
- RTSP:   `rtsp://localhost:8554/processed`

---

## Extending Detection Logic

To add new detection types (helmet, gloves, zone), edit `python_ai_service/detector.py`:

1. In `detect_and_annotate()`, add new class checks:
   ```python
   if class_name == "helmet":
       # helmet detection logic
   ```

2. Add new alert types:
   ```python
   send_alert(camera_id, "NO_HELMET", "Critical")
   ```

3. The Alert entity already supports arbitrary `Type` and `Severity` strings.
=======
# CameraAlerts
>>>>>>> 44fd5e7f74cd010addde6cba33ca73f342ac30c1
