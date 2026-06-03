import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None


def info(msg):
    if RICH_AVAILABLE:
        console.print(Panel(msg, border_style="blue"))
    else:
        print(f"[INFO] {msg}")


def success(msg):
    if RICH_AVAILABLE:
        console.print(f"[bold green]\u2713[/] {msg}")
    else:
        print(f"[OK] {msg}")


def error(msg):
    if RICH_AVAILABLE:
        console.print(f"[bold red]\u2717[/] {msg}")
    else:
        print(f"[ERROR] {msg}", file=sys.stderr)


def sanitize(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r'\s+', " ", name)
    return name[:200].strip()


def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "spotify.com" in url_lower:
        if "/track/" in url_lower:
            return "spotify_track"
        if "/album/" in url_lower:
            return "spotify_album"
        if "/playlist/" in url_lower:
            return "spotify_playlist"
        return "spotify"
    if any(d in url_lower for d in ("youtube.com", "youtu.be", "music.youtube.com")):
        return "youtube"
    if "instagram.com" in url_lower:
        return "instagram"
    if "facebook.com" in url_lower or "fb.watch" in url_lower:
        return "facebook"
    return "generic"


def resolve_output_path(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def run_cmd(cmd: list[str], verbose: bool = False, capture: bool = False):
    if verbose:
        print(f"[CMD] {' '.join(cmd)}", file=sys.stderr)
    try:
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return result
        result = subprocess.run(cmd, check=False)
        return result
    except FileNotFoundError:
        error(f"Command not found: {cmd[0]}. Is it installed?")
        return None


def get_ytdlp_metadata(url: str) -> dict | None:
    cmd = ["yt-dlp", "-J", "--no-download", "--no-warnings", "--flat-playlist", url]
    result = run_cmd(cmd, capture=True)
    if result and result.returncode == 0 and result.stdout:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
    return None


def download_spotify(url: str, output: str, fmt: str, quality: str, verbose: bool) -> int:
    is_album = "/album/" in url.lower()

    if is_album:
        out_template = os.path.join(
            output, "{album-artist} - {album}", "{track-number:02d} - {title}.{ext}"
        )
    else:
        out_template = os.path.join(output, "{artist}", "{title}.{ext}")

    cmd = [
        "spotdl", url,
        "--output", out_template,
        "--format", fmt,
    ]
    if quality:
        cmd.extend(["--bitrate", quality])
    cmd.extend(["--log-level", "INFO" if verbose else "WARNING"])

    label = "Album" if is_album else "Track"
    info(f"Downloading {url}")
    if RICH_AVAILABLE:
        with console.status(f"[bold yellow]Processing {label}...[/]"):
            result = run_cmd(cmd, verbose)
    else:
        print(f"Downloading {label.lower()}...")
        result = run_cmd(cmd, verbose)

    if result and result.returncode == 0:
        success(f"Saved to {output}")
    else:
        error(f"Download failed (spotdl exit code: {result.returncode if result else 'N/A'})")
    return result.returncode if result else 1


def download_ytdlp(url: str, output: str, fmt: str, quality: str,
                   audio_only: bool, verbose: bool) -> int:
    meta = None
    out_dir = output

    if audio_only:
        meta = get_ytdlp_metadata(url)

        if meta:
            is_playlist = meta.get("_type") == "playlist" or "/playlist" in url.lower()

            if is_playlist and meta.get("entries"):
                playlist_title = sanitize(meta.get("title") or "Playlist")
                uploader = sanitize(
                    meta.get("uploader") or meta.get("channel") or "Unknown Artist"
                )
                out_dir = os.path.join(output, uploader, playlist_title)
            else:
                artist = (
                    sanitize(meta.get("artist"))
                    or sanitize(meta.get("creator"))
                    or sanitize(meta.get("uploader"))
                    or "Unknown Artist"
                )
                album = sanitize(meta.get("album")) or ""

                if album:
                    out_dir = os.path.join(output, artist, album)
                else:
                    out_dir = os.path.join(output, artist, "Singles")

    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp", url,
        "-o", out_template,
        "--no-overwrites",
        "--output-na-placeholder", "",
    ]

    if "/playlist" in url.lower() or "&list=" in url.lower():
        cmd.append("--yes-playlist")
    else:
        cmd.append("--no-playlist")

    if audio_only:
        cmd.extend(["-x", "--audio-format", fmt, "--embed-thumbnail", "--add-metadata"])
        if quality:
            cmd.extend(["--audio-quality", quality])
    else:
        if fmt and fmt != "best":
            cmd.extend(["-f", fmt])

    if verbose:
        cmd.append("--verbose")
    else:
        cmd.append("--no-warnings")

    label = "Audio" if audio_only else "Video"
    subtype = "playlist" if "/playlist" in url.lower() else "single"
    info(f"Downloading {url}")
    if RICH_AVAILABLE:
        console.print(f"[dim]{label} / {subtype} -> {out_dir}[/]")
    else:
        print(f"{label} ({subtype}) -> {out_dir}")

    result = run_cmd(cmd, verbose)
    if result and result.returncode == 0:
        success(f"Saved to {out_dir}")
    else:
        error(f"Download failed (yt-dlp exit code: {result.returncode if result else 'N/A'})")
    return result.returncode if result else 1


def main():
    parser = argparse.ArgumentParser(
        description="Download media from YouTube, Instagram, Facebook, or Spotify.",
    )
    parser.add_argument("url", help="URL to download from")
    parser.add_argument("-o", "--output", help="Output directory (auto: ~/Music or ~/Videos)")
    parser.add_argument("-f", "--format", default="mp3",
                        help="Audio format: mp3, flac, opus, m4a, wav (default: mp3)")
    parser.add_argument("-q", "--quality", default="",
                        help="Audio quality: 0-9 (yt-dlp) or bitrate e.g. 320k (spotdl)")
    parser.add_argument("--video", action="store_true",
                        help="Download as video (YouTube/Instagram/Facebook)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    if not RICH_AVAILABLE:
        print("Tip: pip install rich for a better UI", file=sys.stderr)

    platform = detect_platform(args.url)

    if args.output:
        output = resolve_output_path(args.output)
    elif platform.startswith("spotify") or (platform == "youtube" and not args.video):
        output = os.path.expanduser("~/Music")
    else:
        output = os.path.expanduser("~/Videos")

    Path(output).mkdir(parents=True, exist_ok=True)

    if RICH_AVAILABLE:
        tbl = Table.grid(padding=(0, 2))
        tbl.add_column(style="bold cyan")
        tbl.add_column()
        tbl.add_row("URL", args.url)
        tbl.add_row("Platform", platform)
        tbl.add_row("Output", output)
        tbl.add_row("Format", args.format)
        console.print(Panel(tbl, title="[bold]Media Downloader[/]", border_style="green"))

    if platform.startswith("spotify"):
        rc = download_spotify(args.url, output, args.format, args.quality, args.verbose)
    elif platform == "youtube":
        rc = download_ytdlp(args.url, output, args.format, args.quality,
                            not args.video, args.verbose)
    elif platform in ("instagram", "facebook"):
        rc = download_ytdlp(args.url, output, args.format, args.quality,
                            not args.video, args.verbose)
    else:
        error(f"Unknown URL platform: {args.url}")
        return 1

    return rc


if __name__ == "__main__":
    sys.exit(main())
