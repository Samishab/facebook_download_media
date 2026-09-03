"""
Facebook Video Downloader
Downloads all videos from a Facebook profile or page URL using yt-dlp.
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path


def check_yt_dlp():
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def install_yt_dlp():
    print("Installing yt-dlp...")
    subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)


def download_facebook_videos(url: str, output_dir: str, cookies_file: str | None = None):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--ignore-errors",           # skip unavailable videos
        "--no-warnings",
        "--extract-flat", "no",
        "--yes-playlist",            # download all videos in the profile/page
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", str(output_path / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
        "--write-info-json",         # save metadata alongside each video
        "--progress",
        "--concurrent-fragments", "4",
    ]

    if cookies_file:
        cmd += ["--cookies", cookies_file]

    cmd.append(url)

    print(f"\nDownloading videos from:\n  {url}")
    print(f"Saving to: {output_path.resolve()}\n")

    try:
        subprocess.run(cmd, check=True)
        print("\nDone!")
    except subprocess.CalledProcessError as e:
        print(f"\nyt-dlp exited with code {e.returncode}")
        print("Tip: if you see login errors, provide a cookies file with --cookies.")
        sys.exit(e.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="Download all videos from a Facebook profile or page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python downloader.py https://www.facebook.com/profile.php?id=100080241908021
  python downloader.py https://www.facebook.com/SomePage -o videos/SomePage
  python downloader.py https://www.facebook.com/profile.php?id=100080241908021 --cookies cookies.txt
        """,
    )
    parser.add_argument("url", help="Facebook profile or page URL")
    parser.add_argument(
        "-o", "--output",
        default="downloads",
        help="Output directory (default: ./downloads)",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Path to a Netscape-format cookies file for private/login-required profiles",
    )

    args = parser.parse_args()

    if not check_yt_dlp():
        install_yt_dlp()

    download_facebook_videos(args.url, args.output, args.cookies)


if __name__ == "__main__":
    main()
