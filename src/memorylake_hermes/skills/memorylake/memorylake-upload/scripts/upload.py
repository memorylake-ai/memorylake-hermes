#!/usr/bin/env python3
"""MemoryLake multipart upload — single files, archives, and directories.

Usage:
    python upload.py --host HOST --api-key KEY --project-id PID [--name NAME] PATH

Supports:
    - Single files: uploaded directly
    - Archives (.zip, .tar.gz, .tar.bz2, .tar.xz, .7z): extracted, each file uploaded
    - Directories: recursively traversed, all files uploaded (max 500)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import zipfile
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests

MAX_FILES = 500
MAX_WORKERS = 10
TIMEOUT = 15.0
UPLOAD_TIMEOUT = 120.0

ARCHIVE_EXTS = {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz",
    ".tar.gz", ".tar.bz2", ".tar.xz", ".7z",
}


def is_archive(path: str) -> bool:
    p = path.lower()
    for ext in sorted(ARCHIVE_EXTS, key=len, reverse=True):
        if p.endswith(ext):
            return True
    return False


def extract_archive(archive_path: str, dest_dir: str) -> list[str]:
    """Extract archive and return list of extracted file paths."""
    files = []
    lower = archive_path.lower()

    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                # Fix CJK filenames: no UTF-8 flag → CP437 decoded → re-encode and try UTF-8/GBK
                if not (info.flag_bits & 0x800) and not info.filename.isascii():
                    try:
                        raw = info.filename.encode("cp437")
                        info.filename = raw.decode("utf-8")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        try:
                            raw = info.filename.encode("cp437")
                            info.filename = raw.decode("gbk")
                        except (UnicodeDecodeError, UnicodeEncodeError):
                            pass
                zf.extract(info, dest_dir)
                if not info.is_dir():
                    files.append(os.path.join(dest_dir, info.filename))
    elif lower.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar")):
        with tarfile.open(archive_path) as tf:
            tf.extractall(dest_dir, filter="data")
            files = [
                os.path.join(dest_dir, m.name) for m in tf.getmembers()
                if m.isfile()
            ]
    elif lower.endswith(".gz") and not lower.endswith(".tar.gz"):
        import gzip
        out_name = os.path.basename(archive_path).rsplit(".gz", 1)[0] or "extracted"
        out_path = os.path.join(dest_dir, out_name)
        with gzip.open(archive_path, "rb") as gf, open(out_path, "wb") as of:
            of.write(gf.read())
        files = [out_path]
    elif lower.endswith(".bz2") and not lower.endswith(".tar.bz2"):
        import bz2
        out_name = os.path.basename(archive_path).rsplit(".bz2", 1)[0] or "extracted"
        out_path = os.path.join(dest_dir, out_name)
        with bz2.open(archive_path, "rb") as bf, open(out_path, "wb") as of:
            of.write(bf.read())
        files = [out_path]
    elif lower.endswith(".xz") and not lower.endswith(".tar.xz"):
        import lzma
        out_name = os.path.basename(archive_path).rsplit(".xz", 1)[0] or "extracted"
        out_path = os.path.join(dest_dir, out_name)
        with lzma.open(archive_path, "rb") as xf, open(out_path, "wb") as of:
            of.write(xf.read())
        files = [out_path]
    elif lower.endswith(".7z"):
        try:
            import py7zr
            with py7zr.SevenZipFile(archive_path, "r") as sz:
                sz.extractall(dest_dir)
            for root, _, fnames in os.walk(dest_dir):
                for fn in fnames:
                    files.append(os.path.join(root, fn))
        except ImportError:
            print("ERROR: py7zr not installed. Run: pip install py7zr", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"ERROR: Unsupported archive format: {archive_path}", file=sys.stderr)
        sys.exit(1)

    return [f for f in files if os.path.isfile(f)]


def collect_directory_files(dir_path: str) -> list[str]:
    """Recursively collect files from a directory (max MAX_FILES)."""
    files = []
    for root, _, fnames in os.walk(dir_path):
        for fn in fnames:
            if fn.startswith("."):
                continue
            files.append(os.path.join(root, fn))
            if len(files) >= MAX_FILES:
                print(f"WARNING: Reached {MAX_FILES} file limit, skipping remaining files",
                      file=sys.stderr)
                return files
    return files


def headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def create_multipart_upload(host: str, api_key: str, file_size: int) -> dict:
    resp = requests.post(
        f"{host}/openapi/memorylake/api/v1/upload/create-multipart",
        json={"file_size": file_size},
        headers=headers(api_key),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(body.get("message", "create-multipart failed"))
    return body["data"]


def upload_part(url: str, data: bytes) -> str:
    resp = requests.put(
        url,
        data=data,
        headers={"Content-Type": "application/octet-stream"},
        timeout=UPLOAD_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.headers.get("ETag", "").strip('"')


def complete_multipart(host: str, api_key: str, upload_id: str,
                       object_key: str, part_etags: list) -> dict:
    resp = requests.post(
        f"{host}/openapi/memorylake/api/v1/upload/complete-multipart",
        json={
            "upload_id": upload_id,
            "object_key": object_key,
            "part_etags": part_etags,
        },
        headers=headers(api_key),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(body.get("message", "complete-multipart failed"))
    return body.get("data", {})


def quick_add_document(host: str, api_key: str, project_id: str,
                       object_key: str, file_name: str) -> dict:
    resp = requests.post(
        f"{host}/openapi/memorylake/api/v1/projects/{project_id}/documents/quick-add",
        json={"object_key": object_key, "file_name": file_name},
        headers=headers(api_key),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(body.get("message", "quick-add failed"))
    return body.get("data", {})


def upload_single_file(host: str, api_key: str, project_id: str,
                       file_path: str, display_name: str | None = None) -> dict:
    """Upload a single file via multipart upload and add to project."""
    file_size = os.path.getsize(file_path)
    file_name = display_name or os.path.basename(file_path)
    print(f"  Uploading: {file_name} ({file_size:,} bytes)")

    # Step 1: Create
    info = create_multipart_upload(host, api_key, file_size)
    upload_id = info["upload_id"]
    object_key = info["object_key"]
    part_items = info.get("part_items", [])

    if not part_items:
        raise RuntimeError("No part_items returned")

    # Step 2: Upload parts
    part_etags = []
    with open(file_path, "rb") as f:
        for part in part_items:
            data = f.read(part["size"])
            if not data:
                break
            etag = upload_part(part["upload_url"], data)
            part_etags.append({"number": part["number"], "etag": etag})

    # Step 3: Complete
    complete_multipart(host, api_key, upload_id, object_key, part_etags)

    # Step 4: Add to project
    result = quick_add_document(host, api_key, project_id, object_key, file_name)
    print(f"  Done: {file_name}")
    return result


def upload_path(host: str, api_key: str, project_id: str,
                path: str, display_name: str | None = None) -> tuple[int, int]:
    """Upload a file, archive, or directory to MemoryLake.

    Handles type detection automatically:
    - Directory: recursively collects files and uploads each
    - Archive: extracts then uploads each file inside
    - Single file: uploads directly

    Returns (succeeded, failed) counts.
    """
    target = os.path.abspath(path)
    if not os.path.exists(target):
        raise FileNotFoundError(f"Path does not exist: {target}")

    files_to_upload: list[tuple[str, str | None]] = []  # (path, display_name)

    if os.path.isdir(target):
        print(f"Directory: {target}")
        collected = collect_directory_files(target)
        print(f"  Found {len(collected)} files")
        files_to_upload = [(f, None) for f in collected]
    elif is_archive(target):
        print(f"Archive: {target}")
        tmp_dir = tempfile.mkdtemp(prefix="memorylake-extract-")
        extracted = extract_archive(target, tmp_dir)
        print(f"  Extracted {len(extracted)} files")
        files_to_upload = [(f, None) for f in extracted]
    else:
        files_to_upload = [(target, display_name)]

    if not files_to_upload:
        return 0, 0

    succeeded = 0
    failed = 0

    if len(files_to_upload) == 1:
        fp, name = files_to_upload[0]
        try:
            upload_single_file(host, api_key, project_id, fp, name)
            succeeded = 1
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            failed = 1
    else:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(
                    upload_single_file, host, api_key, project_id, fp, name
                ): fp
                for fp, name in files_to_upload
            }
            for future in as_completed(futures):
                fp = futures[future]
                try:
                    future.result()
                    succeeded += 1
                except Exception as e:
                    print(f"  FAILED {os.path.basename(fp)}: {e}", file=sys.stderr)
                    failed += 1

    return succeeded, failed


def main():
    parser = argparse.ArgumentParser(description="Upload files to MemoryLake")
    parser.add_argument("path", help="File, archive, or directory to upload")
    parser.add_argument("--host", required=True, help="MemoryLake host URL")
    parser.add_argument("--api-key", required=True, help="API key")
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--name", help="Override filename for single file upload")
    args = parser.parse_args()

    succeeded, failed = upload_path(
        args.host.rstrip("/"), args.api_key, args.project_id,
        args.path, args.name,
    )
    total = succeeded + failed
    print(f"\nSummary: {succeeded} uploaded, {failed} failed, {total} total")
    sys.exit(1 if failed > 0 and succeeded == 0 else 0)


if __name__ == "__main__":
    main()
