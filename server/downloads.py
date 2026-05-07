from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Dict, Any, List
from urllib.parse import quote

import requests


DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "downloads")).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str) -> str:
    keep = []
    for ch in name or "download.bin":
        if ch.isalnum() or ch in (".", "-", "_", " "):
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip().strip(".")
    return out[:180] or "download.bin"


def unique_path(filename: str) -> Path:
    base = safe_filename(filename)
    path = DOWNLOAD_DIR / base
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = DOWNLOAD_DIR / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return DOWNLOAD_DIR / f"{stem}-{os.getpid()}{suffix}"


def file_info(path: Path, url: str | None = None) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "url": url,
        "release_url": None,
        "uploaded": False,
    }


def list_downloads() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for p in sorted(DOWNLOAD_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.name != ".gitkeep":
            items.append(file_info(p))
    return items


def github_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def ensure_release(repo: str, token: str, tag: str = "browser-downloads") -> Dict[str, Any]:
    headers = github_headers(token)
    base = f"https://api.github.com/repos/{repo}"
    r = requests.get(f"{base}/releases/tags/{quote(tag, safe='')}", headers=headers, timeout=30)
    if r.status_code == 200:
        return r.json()
    if r.status_code != 404:
        raise RuntimeError(f"GitHub release lookup failed: {r.status_code} {r.text[:300]}")
    payload = {
        "tag_name": tag,
        "name": "Browser Downloads",
        "body": "Files captured by Git Live Browser.",
        "draft": False,
        "prerelease": False,
    }
    r = requests.post(f"{base}/releases", headers=headers, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub release create failed: {r.status_code} {r.text[:300]}")
    return r.json()


def upload_to_release(path: Path, repo: str | None = None, token: str | None = None, tag: str = "browser-downloads") -> Dict[str, Any]:
    repo = repo or os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY")
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        raise RuntimeError("برای آپلود در Release باید GH_TOKEN و REPO تنظیم شده باشد.")

    release = ensure_release(repo, token, tag)
    upload_url = release["upload_url"].split("{")[0]
    name = path.name

    # Delete existing asset with same name to avoid 422.
    headers = github_headers(token)
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            requests.delete(asset["url"], headers=headers, timeout=30)
            break

    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    upload_headers = dict(headers)
    upload_headers["Content-Type"] = content_type
    with path.open("rb") as f:
        r = requests.post(
            f"{upload_url}?name={quote(name)}",
            headers=upload_headers,
            data=f,
            timeout=300,
        )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub release asset upload failed: {r.status_code} {r.text[:300]}")
    asset = r.json()
    return {
        "name": name,
        "size": path.stat().st_size,
        "browser_download_url": asset.get("browser_download_url"),
        "release_url": release.get("html_url"),
    }
