# immich_family_sync

A lightweight, reliable **many‑to‑one Immich media synchronizer** designed for families or groups who want a simple “dropbox‑style” workflow for sharing photos and videos.

This script automatically:

- Pulls media from **multiple source accounts’ Outbox albums**
- Uploads them into a **shared destination account’s Inbox album**
- Deletes the originals from the source accounts
- Removes Inbox items that have already been added to shared albums
- Cleans up stale items left in the Inbox for too long

It’s a hands‑off ingestion pipeline that keeps your shared Immich library tidy and organized.

---

## 🌟 Why this exists

Immich is fantastic for shared family photo libraries, but there’s no built‑in “inbox” workflow.
This script fills that gap:

- Each family member dumps photos into their **Outbox** album.
- The script collects everything into a **shared account’s Inbox**.
- The shared account owner organizes items into shared albums.
- The script automatically removes Inbox items once they’ve been placed elsewhere.
- Anything left unorganized for too long gets cleaned up.

It’s simple, predictable, and keeps everyone’s library clean.

---

# 🚀 Features

### ✔ Many‑to‑one syncing
Pulls media from multiple source accounts and consolidates them into a single shared account.

### ✔ Metadata‑preserving uploads
Retains:
- Original filename
- Creation & modification timestamps
- Device IDs
- Favorite flag
- Duration (for videos)

### ✔ Smart download strategy
- Small files → kept in memory
- Large files → streamed to disk (`TEMP_DIR`)

### ✔ Duplicate‑safe uploads
Uses SHA‑1 checksums and handles Immich’s `409 Conflict` responses gracefully.

### ✔ Automatic cleanup
1. **Inbox → Shared albums cleanup**
 If an asset appears in *any* other album, it is removed from the Inbox.

2. **Inbox → Time‑based cleanup**
 Assets left in the Inbox for more than `N` days (default: 30) are removed from the Inbox.

> Removed assets are **not deleted from the account** — only removed from the Inbox album.

### ✔ Dry‑run mode
Simulate everything without uploading or deleting.

### ✔ Debug logging
Verbose output for troubleshooting.

---

# 📦 How It Works

## 1. Source accounts place media in their Outbox
Each source account has an album named: 0_Family Share - Outbox

Anything placed here will be synced.

---

## 2. Script downloads and uploads media
For each source account:

1. Fetch Outbox album
2. Download each asset
3. Upload to destination account
4. Add to destination Inbox album
5. Delete the original from the source account

Destination Inbox album name: `Family Share - Inbox`

---

## 3. User organizes media
The shared account owner moves items from the Inbox into shared albums.

---

## 4. Script cleans the Inbox
On the next run:

- If an asset is found in **any other album**, it is removed from the Inbox.
- If an asset has been in the Inbox longer than `older_than_days`, it is removed.

This keeps the Inbox fresh and prevents clutter.

---

# ⚙️ Configuration

Edit the config section at the top of the script:

```python
INBOX_ALBUM_NAME = "Family Share - Inbox"
OUTBOX_ALBUM_NAME = "0_Family Share - Outbox"
REMOVE_INBOX_IF_PRESENT_IN_ANY_ACCOUNT = False

IMMICH_BASE_URL = "http://127.0.0.1:2283"
SOURCE_API_KEYS = ["KEY1", "KEY2"]
DEST_API_KEY = "DESTKEY"

MAX_IN_MEMORY_SIZE_MB = int(os.getenv("IMMICH_MAX_IN_MEMORY_MB", "30"))
TEMP_DIR = "/tmp/fa-tmp"
```


### Environment variable support
You can override memory limits via: 
```IMMICH_MAX_IN_MEMORY_MB=50```


---

# 🏃 Usage

### Basic run
```python3 immich_family_sync.py```

### Dry run (no uploads or deletions)
```python3 immich_family_sync.py --dryrun```

### Debug logging
```python3 immich_family_sync.py --debug```

### Combine flags
```python3 immich_family_sync.py --dryrun --debug```

---

# 🧼 Cleanup Behavior

### 1. Cross‑album cleanup
If an asset in the Inbox also appears in:

- Any other album in the destination account
- (Optionally) any album in source accounts

…it is removed from the Inbox.

### 2. Age‑based cleanup
Assets older than `older_than_days` (default: 30) are removed from the Inbox.

These removals **do not delete the asset** — they only remove it from the Inbox album.

---

# 📁 Folder & File Handling

### Memory vs disk
- Files ≤ `MAX_IN_MEMORY_SIZE_MB` → stored in RAM
- Larger files → streamed to disk at `TEMP_DIR`

Temporary files are deleted immediately after upload.

---

# 🛡 Error Handling

The script:

- Catches and logs per‑asset errors
- Continues processing remaining assets
- Prints a final summary:

```Uploaded: X, Dry-run skipped: Y, Errors: Z```

---

# 🧪 Example Workflow

1. Alice, Bob, and Charlie each drop photos into their Outbox album.
2. Script runs (manually or via cron).
3. All media appears in the shared account’s Inbox.
4. The shared account owner organizes items into shared albums.
5. Next run:
 - Items already placed in shared albums disappear from Inbox.
 - Items left untouched for too long also disappear.

---

# 📜 License (MIT)

```
MIT License

Copyright (c) 2025 Valerio Fuoglio

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
