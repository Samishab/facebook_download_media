# Facebook Video Downloader

Download all videos from a Facebook profile or page using [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Requirements

- Python 3.10+
- yt-dlp (installed automatically on first run, or manually via `pip install yt-dlp`)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Basic — public profile or page

```bash
python downloader.py "https://www.facebook.com/profile.php?id=100080241908021"
```

### Custom output folder

```bash
python downloader.py "https://www.facebook.com/SomePage" -o videos/SomePage
```

### Private profile (requires login cookies)

Facebook restricts most profile videos to logged-in users.  
Export your browser cookies to a `cookies.txt` file (Netscape format) using a browser extension such as
[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc), then:

```bash
python downloader.py "https://www.facebook.com/profile.php?id=100080241908021" --cookies cookies.txt
```

## Output

Videos are saved in the `downloads/` folder (or the folder you specify with `-o`) as MP4 files.  
A `.info.json` metadata file is saved next to each video.

## Notes

- Only **public** videos are accessible without a cookies file.
- Facebook may throttle or block automated downloads — use responsibly.
- Downloaded videos are for personal use only; respect copyright and Facebook's Terms of Service.
