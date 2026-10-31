"""Command-line front end. Run `python app.py` for the GUI."""

import argparse
import sys
from pathlib import Path

import fbcore


def main():
    parser = argparse.ArgumentParser(description="Download videos from / save offline a Facebook profile or page.")
    parser.add_argument("url", help="Facebook profile or page URL")
    parser.add_argument("-o", "--output", default="downloads", help="Output directory (default: ./downloads)")
    parser.add_argument("--cookies", default=None, help="Path to Netscape cookies.txt file")
    parser.add_argument("--scrolls", type=int, default=0, help="Max scroll attempts (default: 0 = whole page)")
    parser.add_argument("--offline", action="store_true", help="Save the page as an offline browsable copy")
    parser.add_argument("--no-videos", action="store_true", help="Skip downloading videos")
    parser.add_argument("--show-browser", action="store_true", help="Show the browser window")
    parser.add_argument("--no-dates", action="store_true", help="Offline copy: skip reading post dates (faster)")
    parser.add_argument("--order", choices=["orig", "desc", "asc"], default="orig",
                        help="Offline copy order: page order, newest first (desc) or oldest first (asc)")
    parser.add_argument("--pdf", action="store_true", help="Offline copy: also export a PDF")
    args = parser.parse_args()

    headless = not args.show_browser
    fbcore.ensure_yt_dlp()
    if args.offline:
        index = fbcore.archive_page(args.url, Path(args.output) / "offline", args.cookies, args.scrolls,
                                    headless, with_videos=not args.no_videos, with_dates=not args.no_dates,
                                    order=args.order, make_pdf=args.pdf)
        print(f"Open: {index.resolve()}")
        return
    urls = fbcore.scrape_video_urls(args.url, args.cookies, args.scrolls, headless)
    if not urls:
        print("No videos found. Make sure the profile is public or provide valid --cookies.")
        sys.exit(1)
    fbcore.download_videos(urls, Path(args.output) / "videos", args.cookies)


if __name__ == "__main__":
    main()
