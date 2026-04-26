# YouTube URL Downloader

Download from a YouTube **video URL** or **playlist URL**.

The script will:
- ask for URL (or use CLI URL argument)
- inspect available video qualities
- let you choose a quality (e.g. 1080p, 720p, etc.)
- offer **audio-only** option
- show a live status bar with percent, speed, ETA, and playlist item progress

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python youtube_downloader.py
```

GUI app:

```bash
python youtube_downloader_gui.py
```

Or pass URL directly:

```bash
python youtube_downloader.py "https://www.youtube.com/watch?v=VIDEO_ID"
python youtube_downloader.py "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```

Optional output folder:

```bash
python youtube_downloader.py "<URL>" --output-dir "D:/my_downloads"
```

Compact progress mode (good for smaller terminals):

```bash
python youtube_downloader.py "<URL>" --compact-progress
```

## Notes

- Tries to use Chrome cookies first (for content that requires login), then falls back without cookies.
- Status bar uses ANSI colors when terminal supports it.
- Downloads are saved under:
  - `~/Downloads/youtube_downloads/<playlist-or-NA_playlist>/`
- GUI includes:
  - URL-only input
  - quality picker (video resolutions + audio-only)
  - output-folder browser
  - start/cancel/open folder/clear log actions
  - live progress bar + status line + log panel
