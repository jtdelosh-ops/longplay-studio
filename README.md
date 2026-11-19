# Longplay Studio

**Turn a YouTube video into hours of looping audio, ready for your iPhone.**

Paste a video link, choose a listening duration, and download an audio file. Longplay Studio looks for similar passages near the beginning and end, blends the loop transition, and preserves the original intro.

It runs on your computer. You can use its web interface on that computer or from an iPhone connected to the same Wi-Fi.

## What it does

- Creates audio from **1 minute to 8 hours** long.
- Automatically selects loop points and applies a crossfade of up to two seconds.
- Shows the selected loop timestamps.
- Exports **M4A/AAC for iPhone** or **MP3** at a target 192 kbps.
- Includes a Windows launcher that prepares dependencies and opens your browser.
- Provides progress tracking and a download list API for a separate transfer app.

The exported file already contains all the repetitions. You do not need to enable repeat in your music player. Both M4A and MP3 can play on iPhone.

## Get started on Windows

1. On this repository's page, click **Code → Download ZIP**.
2. Right-click the downloaded ZIP and choose **Extract All**. Open the extracted project folder.
3. Double-click **Start-Longplay.cmd**. You do not normally need administrator mode.
4. Keep the terminal window open. The app opens in your browser when it is ready.
5. Paste a public YouTube video link, choose a duration and format, then click **Create longplay**.
6. Download the finished audio.

On its first launch, the launcher creates a local Python environment and installs the app dependencies. If Python 3.11 or newer is missing, it attempts to install Python 3.12 using Windows Package Manager. If FFmpeg is missing, it downloads a portable build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) into the project's `tools` folder. These steps need internet access and may take a few minutes.

If automatic Python installation is unavailable, install [Python 3.12 for Windows](https://www.python.org/downloads/windows/) and run the launcher again.

### Opening the app

The launcher prints an **On this PC** address. It normally uses:

**http://127.0.0.1:3000**

If port 3000 is already occupied, it selects another port and prints the address to use. If the browser does not open automatically, copy the printed address into its address bar.

To stop the app, press **Ctrl+C** in its terminal window. Keep the computer awake while a conversion is running.

## Use it on iPhone

1. Start Longplay Studio on your Windows PC.
2. Connect the PC and iPhone to the same trusted Wi-Fi network.
3. In iPhone Safari, open an address printed under **On iPhone** in the launcher window. Use the PC's network address, not `localhost`.
4. Choose **M4A · iPhone**, create your audio, and download it.
5. Open the downloaded file and use **Share → Save to Files**, or import the downloaded file through your transfer app.

If Windows Firewall prompts you, allow Python on **Private networks** for your home Wi-Fi. Guest-network isolation and VPN settings can prevent the phone from reaching the PC.

The phone is the interface; your PC performs the conversion. Once the audio is downloaded to your phone, a compatible player can play it offline. The website itself is not an offline app. Transferring a file does not automatically add it to the Apple Music library.

## Choose an audio format

| Format | Use it for | Encoding |
| --- | --- | --- |
| **M4A** (default) | iPhone playback and audio transfer apps | AAC-LC, target 192 kbps, 44.1 kHz, stereo |
| **MP3** | Broad compatibility with other players | MP3, 192 kbps, 44.1 kHz, stereo |

M4A exports include streaming-friendly metadata placement. Both formats include title, artist, and album tags. At 192 kbps, allow roughly **86 MB per hour**, or **691 MB for eight hours**; actual sizes vary.

For another Codex project building a transfer app, see [the transfer integration guide](TRANSFER-APP-HANDOFF.txt). It documents supported files and the local export/download API. That separate app is not included in this repository.

## How smart looping works

Longplay Studio compares loudness and spectral patterns in passages within the first and last 20% of the source, searching up to 60 seconds at each end. It chooses similar passages, avoids silence when audible alternatives exist, and blends the end back into the beginning. The original intro plays once, followed by the selected loop region.

Short sources under ten seconds use their full extent with a shorter crossfade. A lossless intermediate loop is encoded into one continuous output file, avoiding separately encoded chunks at each repetition.

This works best with steady music and ambience. Speech, changing rhythms, or contrasting sections can still have a noticeable transition. It does not perform beat tracking or guarantee an inaudible transition for every video. If your requested duration ends within the original intro, the output is simply trimmed to that duration.

## Install manually: Windows, macOS, or Linux

You need **Python 3.11+**, **FFmpeg and FFprobe** available on your PATH, and internet access. FFmpeg must include its AAC encoder and `libmp3lame`.

Clone the repository, then enter its folder:

```sh
git clone https://github.com/jtdelosh-ops/longplay-studio.git
cd longplay-studio
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 3000
```

On macOS or Linux (use `python3` to create the environment if needed):

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 3000
```

Open **http://127.0.0.1:3000**. To allow access from your phone on the local network, change `--host 127.0.0.1` to `--host 0.0.0.0`, then open the PC's LAN IP and port on the phone.

The Windows launcher automatically finds its portable FFmpeg; a manual launch needs FFmpeg and FFprobe on PATH. On Debian/Ubuntu, install FFmpeg with `sudo apt-get install ffmpeg`. On macOS with Homebrew, use `brew install ffmpeg`.

## Limits and file storage

- One conversion runs at a time.
- Source videos must be finished, public videos up to four hours and 512 MB. Restricted, sign-in-only videos and live streams are not supported.
- The app checks for at least 2 GB free disk before starting; large jobs can require additional temporary space.
- Generated files live under `data/<job-id>/`. Completed jobs/files are cleaned up after 24 hours when another job is submitted.
- Job history lives in memory and resets when the server restarts. Audio you have downloaded remains yours to keep.
- The app has no sign-in. Use it locally or on a trusted private network; do not expose it directly to the internet without adding access controls.
- Only download content you own or have permission to use.

The app requires native FFmpeg processes. It needs a normal computer or server rather than static website hosting or a Cloudflare Worker.

## Local API

Use the address printed by your launcher as the base URL.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/jobs` | Create a conversion using `url`, `minutes`, and optional `format` (`m4a` or `mp3`) |
| `GET /api/jobs/{id}` | Read status, progress, loop points, and finished export metadata |
| `GET /api/exports` | List finished files from the current server session |
| `GET /api/jobs/{id}/download` | Download the file |
| `GET /api/jobs/{id}/download?inline=true` | Play the file inline |

Download responses support byte ranges for seeking and resumable transfers. A second conversion request while a job is active returns HTTP 429. Cross-origin browser access is not enabled; a separate app can integrate through its backend. See [the complete integration guide](TRANSFER-APP-HANDOFF.txt) for examples and response fields.

## Troubleshooting

**The launcher window closes or shows an error:** Open the project folder in File Explorer, type `cmd` in its address bar, press Enter, and run `Start-Longplay.cmd`. The command window stays open so you can read the error.

**The app starts but no browser appears:** Copy the printed **On this PC** address into your browser. The launcher checks `127.0.0.1` without a proxy and keeps the server running if its readiness probe cannot connect.

**A YouTube download fails:** Try another public, finished video. YouTube changes regularly. Update yt-dlp through your app environment:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade yt-dlp
```

Some videos also need yt-dlp's current recommended JavaScript runtime setup; consult [yt-dlp's documentation](https://github.com/yt-dlp/yt-dlp#dependencies). Updating is not a guarantee that every video can be downloaded.

**You moved the project and Python stopped working:** Stop the app. Rename its `.venv` folder to `.venv-backup`, then run `Start-Longplay.cmd` to create a fresh environment at the new location. Keep `tools` and `data` so you retain FFmpeg and generated files.

**The phone cannot connect:** Confirm both devices are on the same private network, use the PC's LAN address, and check the private-network firewall permission. The PC must remain awake.

## Development and tests

Install the dependencies and FFmpeg, then run:

```sh
python -m unittest discover -s tests -v
```

Tests synthesize their own audio; they do not download YouTube videos. They cover URL validation, loop matching, smooth joins, export duration, MP3 encoding, AAC/M4A metadata and fast-start layout, and transfer metadata/download headers. GitHub Actions runs the suite on Linux and Windows.

The suite passed in the development Linux workspace. Automated tests do not establish physical iPhone playback or compatibility with a separate transfer app. The Windows launcher requires verification on a Windows machine.

### Project layout

```text
app.py                       API, conversion jobs, and downloads
looping.py                   Audio matching and crossfade preparation
static/index.html            Browser interface
Start-Longplay.cmd            Windows entry point
windows/Start-Longplay.ps1     Windows setup and launcher
requirements.txt             Python dependencies
tests/test_app.py            Audio and API response tests
TRANSFER-APP-HANDOFF.txt       Integration details for a transfer app
```

`data`, `tools`, Python environments, and downloaded dependencies are local runtime files and are excluded from Git.
