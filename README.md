# Universal Video Clipper & Downloader 🎬⚡

A powerful, full-featured local web application and creator studio to **download full-length videos in original 4K UHD** or **intelligently split videos into viral clips** (Sequential, Chapters, Heatmap Peaks) with 9:16 vertical formatting, progress bar overlays, and speech-pause snapping.

Works with **any video URL** (YouTube, TikTok, Twitter/X, Instagram, Vimeo, Reddit, Facebook, Twitch, direct MP4/HLS streams) and **local video uploads**.

---

## ✨ Features

### 1. 📦 Full-Length Video Downloads (Original Form / 4K UHD)
- **No Cuts / Full Video**: Save complete uncut videos directly without splitting.
- **Pristine 4K Resolution**: Pulls the highest uncompressed streams (`bv*+ba/b/best`) up to 2160p (4K UHD) with high-fidelity audio merged into MP4.
- **Dedicated Quick Action Banner**: 1-click download banner right below the metadata card with live estimated file size.
- **Resolution Selector**:
  - 🌟 **Best / 4K UHD (Original Stream)**
  - 📺 **1080p Full HD**
  - ⚡ **720p HD (Ultra Fast)**
  - 🎵 **Audio Only (MP3)**
- **Zero-Loss Fast-Path**: Instantaneous stream copy (`shutil.copy2`) when exporting without filters, preserving 100% original uncompressed quality with zero re-encoding delay.

### 2. ⚡ Turbo Multi-Threaded Download Acceleration
- **Bypasses YouTube Throttling**: Automatically solves YouTube's `n-challenge` via Node.js in milliseconds (`remote_components: ["ejs:github"]`), completely eliminating the 40–80 KB/s playback speed limit.
- **Multi-Connection Downloading**: Integrates `aria2c` with 8 parallel connections and 10 MB HTTP range chunks (`http_chunk_size`), saturating your maximum internet bandwidth.
- **Ultra-Fast Stream Merging**: Prioritizes matching MP4/M4A stream containers, allowing FFmpeg to merge video and audio in under 1 second without transcoding.
- **Result**: Downloads and exports complete in **~3 to 5 seconds** instead of minutes!

### 3. 📁 Permanent Delivery to System Downloads Folder
- **Direct System Downloads Folder**: All full videos and clips are saved directly to your computer's permanent **Downloads folder** (`C:\Users\abhis\Downloads` on Windows / `~/Downloads` on macOS & Linux).
- **Never Deleted**: Unlike internal scratch or staging directories, files in your Downloads folder are **permanent** and never wiped by server cleanup.
- **Duplicate Protection**: Automatically appends `_1`, `_2` if a file with the same title already exists, preventing accidental overwrites.
- **📂 "Show in Folder" Button**: Interactive button in the UI that opens your operating system's File Explorer with the downloaded video selected.

### 4. 🌐 Universal Platform Support (Any Link)
- **1,751+ Supported Video Sites**: Powered by `yt-dlp`'s universal extraction engine.
- **Supported Platforms**:
  - ▶️ **YouTube** (Videos, Shorts, Live streams, VODs)
  - 🎵 **TikTok**
  - 🐦 **X / Twitter**
  - 📸 **Instagram** (Reels, Posts)
  - 🤖 **Reddit**
  - 🎬 **Vimeo**
  - 📘 **Facebook**
  - 👾 **Twitch** (Clips & VODs)
  - 🌐 **Direct Streams** (`.mp4`, `.webm`, `.m3u8` HLS streams)
- **Automated Platform Detection**: Dynamic badge displaying platform brand, icon, and colors.
- **Remote Stream Probing**: Automatic `ffprobe` and HTTP `Content-Length` probing for direct links to fetch accurate duration and file size estimates.

### 5. 📁 Local File Uploads
- **Drag-and-Drop Dropzone**: Select or drop local video files (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`).
- **Instant Metadata Extraction**: Computes exact local file duration and size instantly without re-uploading.

### 6. ✂️ Smart Clipping Strategies
Choose from 4 distinct extraction strategies:
1. **⏱️ Sequential Split**:
   - Cut every $X$ seconds (customizable from 3s to 600s).
   - Optional custom time range trimmer (`MM:SS` start to `MM:SS` end).
2. **📑 By Chapters**:
   - Automatically detects video chapters, timestamps, and titles.
   - Exports each chapter as a standalone clip named after its chapter title.
3. **🔥 Viral Heatmap Peaks**:
   - Detects YouTube's audience retention graph (`most replayed` data).
   - Automatically clips the highest viewer replay peaks and ranks them by retention score.
4. **📦 Full Video (Original / 4K)**:
   - Downloads the full, uninterrupted video in 4K or 1080p.

### 7. 🤫 Smart Speech-Pause Snapping
- Avoids awkward mid-sentence cuts.
- Uses FFmpeg's `silencedetect` audio filter to automatically identify natural speech pauses within a dynamic boundary window and snap cut points to silence.

### 8. 📱 Creator Suite & Aesthetics
- **Aspect Ratio Conversion**:
  - **16:9 Landscape**: Original widescreen format.
  - **9:16 Vertical (Blur)**: Viral Shorts/Reels/TikTok style with blurred background pillars.
  - **9:16 Vertical (Center Crop)**: Focused center crop for vertical mobile viewing.
- **⏳ Dynamic Progress Bar Overlay**: Burns an animated horizontal progress indicator at the bottom of each clip (TikTok/Reels creator trend).
- **🎵 Audio Extraction Mode**: Extracts pristine MP3 audio clips for podcasts, soundbites, or voice samples.
- **🏷️ Custom Filename Prefix**: Custom naming convention (e.g. `podcast_part`, `reel_clip`) for organized exports.

### 9. 🗂️ In-Browser Preview & History Drawer
- **In-Browser Player**: Preview clips or full videos instantly in an interactive modal player before downloading.
- **1-Click ZIP Download**: Package and download all generated clips in a single compressed ZIP file.
- **Batch History Management**: Expandable drawer showing previous jobs with clip counts, total MBs, and timestamps, with instant view, re-download, and cleanup options.

---

## 🛠️ Requirements & Prerequisites

- **Python 3.8+**
- **FFmpeg & FFprobe** installed and accessible via your system PATH.

### Installing FFmpeg

#### Windows
- **Via WinGet (Recommended)**:
  ```powershell
  winget install Gyan.FFmpeg
  ```
- **Or via Chocolatey / Scoop**:
  ```powershell
  choco install ffmpeg
  # or
  scoop install ffmpeg
  ```
- **Or Manual**: Download from [ffmpeg.org](https://ffmpeg.org/download.html), extract, and add the `bin` folder to your system `PATH`.

#### macOS
```bash
brew install ffmpeg
```

#### Linux (Ubuntu / Debian)
```bash
sudo apt update && sudo apt install ffmpeg
```

---

## 🚀 Installation & Setup

1. **Clone or Navigate to the Repository**:
   ```bash
   cd yt-clipper
   ```

2. **Create a Virtual Environment (Optional but recommended)**:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install Python Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the Application**:
   ```bash
   python app.py
   ```

5. **Open in Browser**:
   Open **[http://127.0.0.1:5000](http://127.0.0.1:5000)** in Google Chrome, Edge, Firefox, or Safari.

---

## 📖 How to Use

### A. Downloading a Full-Length Video (Original / 4K UHD)
1. Paste any video link (e.g. YouTube 4K video, TikTok, Twitter, Vimeo) into the input box.
2. Wait a second for the **Live Video Metadata Card** and **Download Full Video (No Cuts)** banner to appear.
3. Choose your desired resolution:
   - `🌟 Best / 4K UHD Original Stream`
   - `📺 1080p Full HD`
   - `⚡ 720p HD`
   - `🎵 Audio Only (MP3)`
4. Click **"Download Full Video"**.
5. Once completed, preview the full video in your browser or click **Download** to save it locally.

### B. Clipping Videos for Social Media (Shorts, Reels, TikTok)
1. Paste your video URL or drag-and-drop a local video file.
2. Select your **Clipping Strategy**:
   - **Sequential Split**: Set clip duration (e.g., 30s or 60s).
   - **By Chapters**: Auto-exports each titled video chapter.
   - **Viral Heatmap**: Extracts the top retention replay peaks.
3. Configure your **Creator Options**:
   - Aspect Ratio: `9:16 Vertical (Shorts/Reels Blur)` or `16:9 Landscape`.
   - Toggle `Burn dynamic progress bar on clips`.
   - Toggle `Smart Silence Snapping` to avoid cutting mid-sentence.
   - (Optional) Set custom filename prefix (e.g., `tech_review`).
4. Click **"Clip It"**.
5. When finished, preview any clip in the modal player, download individual clips, or click **"Download All as ZIP"**.

---

## 🔌 API Reference

The app includes a clean REST API backend:

| Endpoint | Method | Description |
| :--- | :---: | :--- |
| `/api/info` | `POST` | Fetches metadata, duration, platform info, chapters, heatmap peaks, and estimated resolution file sizes for any URL. |
| `/api/upload` | `POST` | Uploads a local video file (multipart form data) and returns upload ID, duration, and size. |
| `/api/start` | `POST` | Starts an asynchronous processing job. Accepts `url`, `upload_id`, `strategy` (`sequential`, `chapters`, `viral`, `full`), `quality` (`best`, `1080`, `720`), `aspect_ratio`, `mode`, `snap_silence`, `add_progress_bar`, `prefix`, `start_time`, `end_time`. |
| `/api/status/<job_id>` | `GET` | Returns live job status, progress percentage, detail messages, and generated clip list. |
| `/clips/<job_id>/<filename>` | `GET` | Streams or downloads a generated clip or full video file. |
| `/api/zip/<job_id>` | `GET` | Packages all clips of a completed job into a downloadable `.zip` archive. |
| `/api/history` | `GET` | Returns a list of past clipping jobs with metadata. |
| `/api/delete/<job_id>` | `POST` | Permanently deletes generated clips and metadata for a specific job. |

---

## 📂 Project Structure

```
yt-clipper/
├── app.py                 # Flask server, yt-dlp downloader, FFmpeg pipeline, & API routes
├── requirements.txt       # Python dependencies (Flask, yt-dlp, requests)
├── README.md              # Project documentation & creator studio guide
├── templates/
│   └── index.html         # Responsive creator studio UI, styling, and client script
├── downloads/             # Temporary staging folder for downloaded source videos
└── clips/                 # Output directories organized by job ID
    └── <job_id>/
        ├── meta.json      # Job metadata & clip specifications
        └── *.mp4 / *.mp3  # Output clips and full video files
```

---

## ⚖️ Legal & Disclaimer

- Only use this application on videos that you own, have permission to download, or that are published under open licenses (such as Creative Commons).
- Please respect the Terms of Service and copyright policies of YouTube and all third-party hosting platforms.
- This software is distributed for personal, educational, and content creation workflows.
