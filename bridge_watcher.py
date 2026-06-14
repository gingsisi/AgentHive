#!/usr/bin/env python3
"""
Bot Collective Bridge Watcher — Monitors Google Drive folder for Human Bridge share files.
When a new bot-collective/bridge-share-*.json file appears, processes it:
  Layer 3 PII re-scan → Quality check → Contribute to ChromaDB
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent to path for ChromaDB imports
sys.path.insert(0, os.path.dirname(__file__))

from capture_receiver import process_capture, strip_pii_server

# Lazy-load ChromaDB (only when needed)
_chroma_db = None

def get_db():
    global _chroma_db
    if _chroma_db is None:
        from chroma_manager import ChromaManager
        _chroma_db = ChromaManager()
        log(f"📦 ChromaDB ready: {_chroma_db.get_stats()}")
    return _chroma_db

# ── Config ──
WATCH_DIR = Path(os.path.dirname(__file__))
CAPTURES_DIR = WATCH_DIR / 'captures'
INCOMING_GLOB = 'bridge-share-*.json'  # .json only (processed → .done, errors → .error)
PROCESSED_DIR = CAPTURES_DIR / 'processed'
LOG_FILE = CAPTURES_DIR / 'watcher.log'
SCAN_INTERVAL = 30  # seconds


def log(msg: str):
    """Log with timestamp."""
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def find_incoming_files() -> list[Path]:
    """Find unprocessed bridge-share JSON files."""
    incoming = []
    # Check root of bot-collective folder
    for pattern in [INCOMING_GLOB, f'**/{INCOMING_GLOB}']:
        for f in WATCH_DIR.glob(pattern):
            if f.is_file() and f.suffix == '.json':
                incoming.append(f)
    
    # Also check the captures directory
    for f in CAPTURES_DIR.glob(INCOMING_GLOB):
        if f.is_file():
            incoming.append(f)
    
    return sorted(set(incoming))


def process_file(filepath: Path) -> dict:
    """Process a single bridge-share JSON file."""
    log(f"📥 Processing: {filepath.name}")
    
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        log(f"❌ Invalid JSON: {e}")
        # Rename to .error so we don't re-process
        filepath.rename(filepath.with_suffix('.error'))
        return {'file': filepath.name, 'error': 'invalid_json', 'processed': 0}

    if not isinstance(data, list):
        data = [data]

    results = {'file': filepath.name, 'total': len(data), 'contributed': 0, 'rejected': 0, 'errors': []}
    
    for item in data:
        try:
            result = process_capture(item)
            if result.get('contributed') and result.get('quality_pass'):
                # Actually write to ChromaDB
                try:
                    db = get_db()
                    clean = result.get('clean_content', '')
                    query = result.get('clean_query', item.get('title', item.get('url', '')))
                    tags = item.get('tags', ['human-bridge'])
                    
                    item_id = db.contribute_web_result(
                        query=query,
                        content=clean,
                        source_url=item.get('url', ''),
                        tags=tags,
                        privacy_class='public'
                    )
                    results['contributed'] += 1
                    log(f"  ✅ {item.get('url', '?')[:60]}")
                except Exception as e:
                    results['errors'].append(f'ChromaDB: {e}')
                    log(f"  ❌ ChromaDB error: {e}")
            else:
                results['rejected'] += 1
                log(f"  ⏭️  {item.get('url', '?')[:60]} — {result.get('quality_reason', 'unknown')}")
        except Exception as e:
            results['errors'].append(str(e))
            log(f"  ❌ Error: {e}")

    # Rename to .done instead of moving — avoids Google Drive re-sync loop
    done_path = filepath.with_suffix('.done')
    filepath.rename(done_path)
    log(f"📁 Renamed to {done_path.name}")
    
    return results


def run_once():
    """Single scan pass."""
    files = find_incoming_files()
    if not files:
        return {'files': 0}
    
    results = []
    for f in files:
        r = process_file(f)
        results.append(r)
    
    total_contributed = sum(r.get('contributed', 0) for r in results)
    log(f"📊 Pass complete: {len(files)} files, {total_contributed} items contributed")
    return {'files': len(files), 'results': results}


def run_watch():
    """Continuous watching mode."""
    log("👀 Bridge Watcher started. Watching for bridge-share-*.json files...")
    log(f"   Watch dir: {WATCH_DIR}")
    log(f"   Scan interval: {SCAN_INTERVAL}s")
    
    while True:
        try:
            result = run_once()
            if result.get('files', 0) > 0:
                log("⏳ Waiting for next scan...")
        except Exception as e:
            log(f"⚠️ Scan error: {e}")
        
        time.sleep(SCAN_INTERVAL)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Bot Collective Bridge Watcher')
    parser.add_argument('--once', action='store_true', help='Single scan, then exit')
    args = parser.parse_args()
    
    if args.once:
        result = run_once()
        print(json.dumps(result, indent=2))
    else:
        run_watch()
