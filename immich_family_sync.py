#!/usr/bin/env python3

# -----------------------------------------------------------------------------
# immich_family_sync.py
#
# Copyright (c) 2025 Valerio Fuoglio
# https://github.com/johndoe
#
# This software is released under the MIT License.
# https://opensource.org/licenses/MIT
#
# A many-to-one media synchronization tool for Immich.
# - Collects assets from multiple source Outbox albums
# - Uploads them into a shared destination Inbox album
# - Removes originals from source accounts
# - Cleans Inbox items already added to other albums
# - Removes stale Inbox items after a configurable time
#
# -----------------------------------------------------------------------------


import os
import requests
import argparse
import hashlib
from datetime import datetime, timezone, timedelta

# --- Config ---
INBOX_ALBUM_NAME = "Family Share - Inbox"
OUTBOX_ALBUM_NAME = "0_Family Share - Outbox"
REMOVE_INBOX_IF_PRESENT_IN_ANY_ACCOUNT = False  # If True, check all accounts; if False, only destination

IMMICH_BASE_URL = "http://127.0.0.1:2283"
SOURCE_API_KEYS = ["KEY1","KEY2"]
DEST_API_KEY = "DESTKEY"

# --- Upload/transfer strategy ---
# Parameter: maximum size to keep in memory. Larger files are streamed to disk at TEMP_DIR.
MAX_IN_MEMORY_SIZE_MB = int(os.getenv("IMMICH_MAX_IN_MEMORY_MB", "30"))
MAX_IN_MEMORY_SIZE = MAX_IN_MEMORY_SIZE_MB * 1024 * 1024  # bytes
TEMP_DIR = "/tmp/fa-tmp"

parser = argparse.ArgumentParser(description="Sync assets from Outbox albums to destination Inbox, then delete originals.")
parser.add_argument("--dryrun", action="store_true", help="Don't upload, just simulate actions")
parser.add_argument("--debug", action="store_true", help="Print detailed logs")
args = parser.parse_args()

def debug(msg):
    if args.debug:
        print(f"[DEBUG] {msg}")

def key_fingerprint(k: str) -> str:
    if not k:
        return "none"
    return f"{k[:4]}…{k[-4:]}"

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def coerce_iso8601(value, fallback=None):
    if not value:
        return fallback or now_iso()
    if isinstance(value, str):
        if value.endswith("Z") or "+" in value:
            return value
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)

def parse_iso8601_to_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

# --- Identity / WhoAmI ---
def whoami(base_url, api_key):
    headers = {"Accept": "application/json", "x-api-key": api_key}
    for path in ("/api/auth/me", "/api/users/me"):
        try:
            res = requests.get(f"{base_url}{path}", headers=headers, timeout=20)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
    return {}

# --- Album helpers ---
def get_album_id(base_url, api_key, album_name):
    debug(f"Looking up album ID for: {album_name}")
    url = f"{base_url}/api/albums"
    headers = {"Accept": "application/json", "x-api-key": api_key}
    res = requests.get(url, headers=headers, timeout=60)
    if res.status_code != 200:
        raise Exception(f"Failed to fetch albums: {res.status_code} {res.text}")
    albums = res.json() if isinstance(res.json(), list) else []
    for album in albums:
        name = album.get("albumName") or album.get("name")
        if name == album_name:
            album_id = album.get("id")
            debug(f"Resolved album '{album_name}' to ID: {album_id}")
            return album_id
    raise Exception(f"Album '{album_name}' not found for key {key_fingerprint(api_key)}")

def list_albums(base_url, api_key):
    url = f"{base_url}/api/albums"
    headers = {"Accept": "application/json", "x-api-key": api_key}
    res = requests.get(url, headers=headers, timeout=60)
    if res.status_code != 200:
        raise Exception(f"Failed to fetch albums: {res.status_code} {res.text}")
    albums = res.json() if isinstance(res.json(), list) else []
    return [a for a in albums if isinstance(a, dict)]

def get_album_assets(base_url, api_key, album_id):
    url = f"{base_url}/api/albums/{album_id}"
    headers = {"Accept": "application/json", "x-api-key": api_key}
    res = requests.get(url, headers=headers, timeout=60)
    if res.status_code != 200:
        raise Exception(f"Failed to fetch album info: {res.status_code} {res.text}")
    album = res.json() or {}
    assets = album.get("assets") or []
    if isinstance(assets, dict):
        assets = assets.get("items") or []
    assets = [a for a in assets if isinstance(a, dict)]
    debug(f"Album {album_id} contains {len(assets)} assets")
    return assets

# --- Asset ops ---
def get_asset_metadata(base_url, api_key, asset_id):
    url = f"{base_url}/api/assets/{asset_id}"
    headers = {"Accept": "application/json", "x-api-key": api_key}
    res = requests.get(url, headers=headers, timeout=60)
    if res.status_code != 200:
        raise Exception(f"Failed to get asset metadata for {asset_id}: {res.status_code} {res.text}")
    return res.json()

def download_original(base_url, api_key, asset_id, filename):
    url = f"{base_url}/api/assets/{asset_id}/original"
    headers = {"Accept": "application/octet-stream", "x-api-key": api_key}
    debug(f"Downloading original for asset {asset_id} (max in-mem: {MAX_IN_MEMORY_SIZE_MB} MB)")
    with requests.get(url, headers=headers, stream=True, timeout=300) as res:
        if res.status_code != 200:
            raise Exception(f"Download failed for {asset_id}: {res.status_code} {res.text}")

        content_length_header = res.headers.get("Content-Length")
        content_length = int(content_length_header) if content_length_header and content_length_header.isdigit() else None

        # If size is known and small enough, keep in memory
        if content_length is not None and content_length <= MAX_IN_MEMORY_SIZE:
            data = res.content
            checksum = hashlib.sha1(data).hexdigest()
            return {"use_disk": False, "bytes": data, "checksum": checksum}

        # Otherwise, stream to disk
        os.makedirs(TEMP_DIR, exist_ok=True)
        safe_name = filename or f"{asset_id}.bin"
        path = os.path.join(TEMP_DIR, safe_name)
        sha1 = hashlib.sha1()
        with open(path, "wb") as f:
            for chunk in res.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                sha1.update(chunk)
        return {"use_disk": True, "path": path, "checksum": sha1.hexdigest()}

def add_to_album(base_url, api_key, album_id, asset_id):
    url = f"{base_url}/api/albums/{album_id}/assets"
    headers = {"Content-Type": "application/json", "Accept": "application/json", "x-api-key": api_key}
    payload = {"ids": [asset_id]}
    res = requests.put(url, headers=headers, json=payload, timeout=30)
    if res.status_code not in (200, 204):
        raise Exception(f"Failed to add asset {asset_id} to album {album_id}: {res.status_code} {res.text}")

def remove_from_album(base_url, api_key, album_id, asset_ids):
    if not asset_ids:
        return
    url = f"{base_url}/api/albums/{album_id}/assets"
    headers = {"Content-Type": "application/json", "Accept": "application/json", "x-api-key": api_key}
    payload = {"ids": asset_ids}
    res = requests.delete(url, headers=headers, json=payload, timeout=60)
    if res.status_code not in (200, 204):
        raise Exception(f"Failed to remove assets from album {album_id}: {res.status_code} {res.text}")
    debug(f"Removed {len(asset_ids)} assets from album {album_id}")

def delete_asset(base_url, api_key, asset_id):
    url = f"{base_url}/api/assets"
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    payload = {"force": True, "ids": [asset_id]}
    res = requests.delete(url, headers=headers, json=payload, timeout=30)
    if res.status_code not in (200, 204):
        raise Exception(f"Failed to delete asset {asset_id}: {res.status_code} {res.text}")

# --- Core upload from Outbox to Inbox ---
def upload_media(base_url, source_api_key, dest_api_key, dest_album_id, asset_ids):
    success_count = 0
    skipped_count = 0
    error_count = 0

    debug(f"Using SOURCE key {key_fingerprint(source_api_key)} for read/download")
    debug(f"Using DEST   key {key_fingerprint(dest_api_key)} for upload/album")

    for asset_id in asset_ids:
        try:
            metadata = get_asset_metadata(base_url, source_api_key, asset_id)
            filename = metadata.get("originalFileName") or f"{asset_id}.bin"
            file_created = coerce_iso8601(metadata.get("fileCreatedAt"))
            file_modified = coerce_iso8601(metadata.get("fileModifiedAt"), fallback=file_created)
            device_asset_id = metadata.get("deviceAssetId") or asset_id
            device_id = metadata.get("deviceId") or "fa-sync"
            duration = metadata.get("duration") or "0:00:00.000000"
            is_favorite = "true" if metadata.get("isFavorite", False) else "false"

            if args.dryrun:
                print(f"[DRYRUN] Would upload: {filename} (source asset {asset_id}) and add to Inbox, then delete source")
                skipped_count += 1
                continue

            # Download original and compute checksum
            dl = download_original(base_url, source_api_key, asset_id, filename)
            sha1_checksum = dl["checksum"]

            # Upload to destination
            upload_url = f"{base_url}/api/assets"
            headers = {
                "Accept": "application/json",
                "x-api-key": dest_api_key,
                "x-immich-checksum": sha1_checksum,
            }
            data = {
                "deviceAssetId": device_asset_id,
                "deviceId": device_id,
                "fileCreatedAt": file_created,
                "fileModifiedAt": file_modified,
                "isFavorite": is_favorite,
                "duration": duration
            }

            dest_id = ""
            status = "ok"
            temp_path = dl.get("path") if dl.get("use_disk") else None

            try:
                if dl.get("use_disk"):
                    debug(f"Uploading from disk: {temp_path}")
                    with open(temp_path, "rb") as f:
                        files = {"assetData": (filename, f, "application/octet-stream")}
                        upload_res = requests.post(upload_url, headers=headers, files=files, data=data, timeout=600)
                else:
                    debug(f"Uploading from memory: {filename} ({len(dl['bytes'])} bytes)")
                    files = {"assetData": (filename, dl["bytes"], "application/octet-stream")}
                    upload_res = requests.post(upload_url, headers=headers, files=files, data=data, timeout=600)
            finally:
                if temp_path:
                    try:
                        os.remove(temp_path)
                        debug(f"Deleted temp file: {temp_path}")
                    except Exception as ce:
                        debug(f"Temp file cleanup failed ({temp_path}): {ce}")

            if upload_res.status_code in (200, 201):
                payload = {}
                try:
                    payload = upload_res.json() or {}
                except Exception:
                    payload = {}
                status = payload.get("status") or ("created" if upload_res.status_code == 201 else "ok")
                dest_id = payload.get("id") or payload.get("assetId") or ""
            elif upload_res.status_code == 409:
                try:
                    payload = upload_res.json() or {}
                except Exception:
                    payload = {}
                dest_id = payload.get("existingAssetId") or payload.get("assetId") or payload.get("id") or ""
                status = "exists"
                debug(f"Duplicate detected for {filename}; using existing asset {dest_id}")
            else:
                raise Exception(f"Upload failed: {upload_res.status_code} {upload_res.text}")

            if not dest_id:
                raise Exception("Destination asset ID missing after upload")

            # Add to destination Inbox album
            add_to_album(base_url, dest_api_key, dest_album_id, dest_id)

            # Delete original from source (removes it from Outbox)
            delete_asset(base_url, source_api_key, asset_id)

            print(f"Uploaded: {filename} [{status}]{' -> ' + dest_id if dest_id else ''}")
            success_count += 1

        except Exception as e:
            error_count += 1
            print(f"[ERROR] Asset {asset_id}: {e}")

    return success_count, skipped_count, error_count

# --- Cleanup: remove Inbox assets that exist in other albums ---
def cleanup_inbox_assets_present_elsewhere(base_url, dest_api_key, album_id, source_api_keys):
    try:
        inbox_assets = get_album_assets(base_url, dest_api_key, album_id)
    except Exception as e:
        print(f"[ERROR] Cleanup (other albums): unable to list inbox assets: {e}")
        return 0

    if not inbox_assets:
        debug("Cleanup (other albums): inbox has no assets")
        print(f"Cleanup: no assets to evaluate in album '{INBOX_ALBUM_NAME}' for cross-album duplicates")
        return 0

    inbox_ids = {a.get("id") for a in inbox_assets if isinstance(a, dict) and a.get("id")}
    debug(f"Cleanup (other albums): evaluating {len(inbox_ids)} inbox assets")

    inspect_keys = [dest_api_key] + source_api_keys if REMOVE_INBOX_IF_PRESENT_IN_ANY_ACCOUNT else [dest_api_key]
    other_album_asset_ids = set()

    for key in inspect_keys:
        fp = key_fingerprint(key)
        try:
            albums = list_albums(base_url, key)
        except Exception as e:
            debug(f"Cleanup (other albums): failed to list albums for key {fp}: {e}")
            continue

        debug(f"Cleanup (other albums): key {fp} has {len(albums)} albums")

        for alb in albums:
            alb_id = alb.get("id")
            alb_name = alb.get("albumName") or alb.get("name") or alb_id

            if key == dest_api_key and alb_id == album_id:
                continue

            try:
                assets = get_album_assets(base_url, key, alb_id)
                ids = [a.get("id") for a in assets if isinstance(a, dict) and a.get("id")]
                debug(f"Cleanup (other albums): album '{alb_name}' contains {len(ids)} assets")
                other_album_asset_ids.update(ids)
            except Exception as e:
                debug(f"Cleanup (other albums): failed to list assets for album '{alb_name}' ({alb_id}) with key {fp}: {e}")
                continue

    to_remove_ids = sorted(inbox_ids.intersection(other_album_asset_ids))
    if not to_remove_ids:
        print("Cleanup: no inbox assets found in other albums across accounts")
        return 0

    if args.dryrun:
        print(f"[DRYRUN] Would remove {len(to_remove_ids)} assets from album '{INBOX_ALBUM_NAME}' because they exist in other albums")
        return 0

    removed = 0
    chunk_size = 100
    for i in range(0, len(to_remove_ids), chunk_size):
        chunk = to_remove_ids[i:i + chunk_size]
        try:
            remove_from_album(base_url, dest_api_key, album_id, chunk)
            removed += len(chunk)
        except Exception as e:
            print(f"[ERROR] Cleanup (other albums): failed to remove a chunk of {len(chunk)} assets: {e}")

    print(f"Cleanup: removed {removed} assets from album '{INBOX_ALBUM_NAME}' because they exist in other albums")
    return removed

# --- Cleanup: remove Inbox assets older than N days by updatedAt ---
def cleanup_old_album_assets(base_url, api_key, album_id, older_than_days=30):
    try:
        album_assets = get_album_assets(base_url, api_key, album_id)
    except Exception as e:
        print(f"[ERROR] Cleanup: unable to list album assets: {e}")
        return 0

    if not album_assets:
        debug("Cleanup: album has no assets")
        print(f"Cleanup: no assets to evaluate in album '{INBOX_ALBUM_NAME}'")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    to_remove_ids = []
    for a in album_assets:
        aid = a.get("id")
        updated_at = a.get("updatedAt")
        dt = parse_iso8601_to_dt(updated_at)
        if dt is None:
            try:
                meta = get_asset_metadata(base_url, api_key, aid)
                dt = parse_iso8601_to_dt(meta.get("updatedAt"))
            except Exception as e:
                debug(f"Cleanup: failed to resolve updatedAt for {aid}: {e}")
                continue
        if dt and dt < cutoff:
            to_remove_ids.append(aid)

    if not to_remove_ids:
        print(f"Cleanup: no assets older than {older_than_days} days to remove from album '{INBOX_ALBUM_NAME}'")
        return 0

    if args.dryrun:
        print(f"[DRYRUN] Would remove {len(to_remove_ids)} assets from album '{INBOX_ALBUM_NAME}' (updatedAt older than {older_than_days} days)")
        return 0

    removed = 0
    chunk_size = 100
    for i in range(0, len(to_remove_ids), chunk_size):
        chunk = to_remove_ids[i:i + chunk_size]
        try:
            remove_from_album(base_url, api_key, album_id, chunk)
            removed += len(chunk)
        except Exception as e:
            print(f"[ERROR] Cleanup: failed to remove a chunk of {len(chunk)} assets: {e}")

    print(f"Cleanup: removed {removed} assets from album '{INBOX_ALBUM_NAME}' (updatedAt older than {older_than_days} days)")
    return removed

def main():
    if not IMMICH_BASE_URL:
        raise SystemExit("IMMICH_BASE_URL is required")
    if not DEST_API_KEY:
        raise SystemExit("IMMICH_DEST_API_KEY is required")
    if not SOURCE_API_KEYS:
        raise SystemExit("IMMICH_SOURCE_API_KEYS is required (comma-separated)")

    # Destination account info and Inbox album
    me_dest = whoami(IMMICH_BASE_URL, DEST_API_KEY) or {}
    dest_name = me_dest.get("email") or me_dest.get("name") or me_dest.get("id") or "unknown"

    try:
        dest_inbox_album_id = get_album_id(IMMICH_BASE_URL, DEST_API_KEY, INBOX_ALBUM_NAME)
    except Exception as e:
        raise SystemExit(str(e))

    total_success = total_skipped = total_error = 0

    # For each source account: read Outbox, upload to dest Inbox, delete source
    for idx, source_key in enumerate(SOURCE_API_KEYS, start=1):
        me_src = whoami(IMMICH_BASE_URL, source_key) or {}
        src_name = me_src.get("email") or me_src.get("name") or me_src.get("id") or f"source-{idx}"
        print(f"--- Source account {idx}/{len(SOURCE_API_KEYS)} ({src_name}, key {key_fingerprint(source_key)}) ---")

        try:
            outbox_album_id = get_album_id(IMMICH_BASE_URL, source_key, OUTBOX_ALBUM_NAME)
        except Exception as e:
            print(f"[ERROR] Source account {idx}: {e}")
            total_error += 1
            continue

        try:
            outbox_assets = get_album_assets(IMMICH_BASE_URL, source_key, outbox_album_id)
            asset_ids = [a.get("id") for a in outbox_assets if isinstance(a, dict) and a.get("id")]
            print(f"Found {len(asset_ids)} assets in '{OUTBOX_ALBUM_NAME}'")
            s, k, e = upload_media(IMMICH_BASE_URL, source_key, DEST_API_KEY, dest_inbox_album_id, asset_ids)
            total_success += s
            total_skipped += k
            total_error += e
        except Exception as e:
            print(f"[ERROR] Source account {idx}: {e}")
            total_error += 1

    # Cleanup step 1: remove Inbox assets if they exist in other albums
    try:
        cleanup_inbox_assets_present_elsewhere(IMMICH_BASE_URL, DEST_API_KEY, dest_inbox_album_id, SOURCE_API_KEYS)
    except Exception as e:
        print(f"[ERROR] Cleanup (other albums) failed: {e}")

    # Cleanup step 2: remove Inbox assets older than 30 days by updatedAt
    try:
        cleanup_old_album_assets(IMMICH_BASE_URL, DEST_API_KEY, dest_inbox_album_id, older_than_days=30)
    except Exception as e:
        print(f"[ERROR] Cleanup step failed: {e}")

    print("--- Summary ---")
    print(f"Uploaded: {total_success}, Dry-run skipped: {total_skipped}, Errors: {total_error}")

if __name__ == "__main__":
    main()

