"""
Facebook Video Downloader
Scrapes video URLs from a Facebook profile/page using Playwright,
then downloads each video with yt-dlp.
"""

import argparse
import subprocess
import sys
from pathlib import Path


# ── dependency helpers ─────────────────────────────────────────────────────────

def ensure_yt_dlp():
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Installing yt-dlp...")
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)


def ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Installing playwright...")
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
    # install browser binary if missing
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


# ── cookie helpers ─────────────────────────────────────────────────────────────

def load_netscape_cookies(cookies_file: str) -> list[dict]:
    cookies = []
    with open(cookies_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, expires, name, value = parts[:7]
            cookie: dict = {
                "domain": domain,
                "path": path,
                "name": name,
                "value": value,
                "secure": secure.upper() == "TRUE",
            }
            if expires.lstrip("-").isdigit() and int(expires) > 0:
                cookie["expires"] = int(expires)
            cookies.append(cookie)
    return cookies


# ── scraping ───────────────────────────────────────────────────────────────────

def build_videos_url(profile_url: str) -> str:
    url = profile_url.rstrip("/")
    if "profile.php" in url:
        sep = "&" if "?" in url else "?"
        if "sk=videos" not in url:
            url += sep + "sk=videos"
    elif "/videos" not in url:
        url += "/videos"
    return url


def scrape_video_urls(profile_url: str, cookies_file: str | None, max_scrolls: int = 30) -> list[str]:
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    videos_page = build_videos_url(profile_url)
    print(f"Opening: {videos_page}\n")

    video_urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        if cookies_file:
            try:
                cookies = load_netscape_cookies(cookies_file)
                context.add_cookies(cookies)
                print(f"Loaded {len(cookies)} cookies from {cookies_file}")
            except Exception as e:
                print(f"Warning: could not load cookies — {e}")

        page = context.new_page()
        page.goto(videos_page, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3_000)

        no_change = 0
        prev_count = 0

        for i in range(max_scrolls):
            try:
                links: list[str] = page.eval_on_selector_all(
                    "a[href]",
                    """els => els.map(e => e.href).filter(h =>
                        h.includes('/videos/') ||
                        h.includes('watch?v=') ||
                        h.includes('watch/?v=') ||
                        h.includes('/reel/')
                    )""",
                )
                video_urls.update(links)
            except Exception:
                pass

            print(f"  Scroll {i + 1:2d}/{max_scrolls} — {len(video_urls)} video URLs found", end="\r")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_000)

            if len(video_urls) == prev_count:
                no_change += 1
                if no_change >= 4:
                    print(f"\nNo new videos found after {no_change} scrolls. Stopping scroll.")
                    break
            else:
                no_change = 0
            prev_count = len(video_urls)

        print()
        browser.close()

    return list(video_urls)


# ── downloading ────────────────────────────────────────────────────────────────

def download_videos(video_urls: list[str], output_dir: str, cookies_file: str | None):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"\nFound {len(video_urls)} videos. Downloading to: {output_path.resolve()}\n")

    failed: list[str] = []

    for idx, url in enumerate(video_urls, 1):
        print(f"[{idx}/{len(video_urls)}] {url}")
        cmd = [
            "yt-dlp",
            "--ignore-errors",
            "--no-warnings",
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--output", str(output_path / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
            "--concurrent-fragments", "4",
        ]
        if cookies_file:
            cmd += ["--cookies", cookies_file]
        cmd.append(url)

        result = subprocess.run(cmd)
        if result.returncode != 0:
            failed.append(url)

    print(f"\nDone! {len(video_urls) - len(failed)}/{len(video_urls)} downloaded.")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for u in failed:
            print(f"  {u}")


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download all videos from a Facebook profile or page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python downloader.py https://www.facebook.com/profile.php?id=100080241908021 --cookies cookies.txt
  python downloader.py https://www.facebook.com/SomePage --cookies cookies.txt -o videos/
        """,
    )
    parser.add_argument("url", help="Facebook profile or page URL")
    parser.add_argument("-o", "--output", default="downloads", help="Output directory (default: ./downloads)")
    parser.add_argument("--cookies", default=None, help="Path to Netscape cookies.txt file (export from browser)")
    parser.add_argument("--scrolls", type=int, default=30, help="Max scroll attempts on the videos page (default: 30)")

    args = parser.parse_args()

    ensure_yt_dlp()

    video_urls = scrape_video_urls(args.url, args.cookies, args.scrolls)

    if not video_urls:
        print("\nNo videos found. Make sure:\n  1. The profile is public, or\n  2. You provided a valid --cookies file from a logged-in browser session.")
        sys.exit(1)

    download_videos(video_urls, args.output, args.cookies)


if __name__ == "__main__":
    main()
