#!/usr/bin/env python3
"""
Curator — Periodic deduplication and cluster cleanup for Bot Collective.
Run as a daily cron job. Merges near-duplicate entries, generates a report.

Usage: python3 curator.py [--dry-run] [--threshold 0.15]
"""

import argparse
import sys
import time
from collections import defaultdict

import chromadb
from chromadb.config import Settings

import os as _os
DB_PATH = _os.path.join(_os.path.dirname(__file__), "chroma_data")


def connect():
    return chromadb.PersistentClient(
        path=DB_PATH, settings=Settings(anonymized_telemetry=False)
    )


def get_all_entries(client, collection_name):
    """Fetch all entries from a collection."""
    try:
        coll = client.get_collection(collection_name)
        data = coll.get()
        return list(zip(
            data["ids"],
            data.get("documents", [""] * len(data["ids"])),
            data.get("metadatas", [{}] * len(data["ids"])),
        ))
    except Exception:
        return []


def find_clusters(entries, threshold=0.15):
    """
    Find clusters of near-duplicate entries using ChromaDB's own search.
    For each entry, query the collection for its nearest neighbors.
    Group entries where distance < threshold.
    """
    client = connect()
    coll = client.get_collection("web_cache")
    
    clustered = set()
    clusters = []
    
    for i, (eid, doc, meta) in enumerate(entries):
        if eid in clustered:
            continue
        
        try:
            results = coll.query(query_texts=[doc[:2000]], n_results=10)
        except Exception:
            continue
        
        cluster = []
        for j, rid in enumerate(results["ids"][0]):
            dist = results["distances"][0][j]
            if rid not in clustered and dist < threshold:
                cluster.append((rid, dist, results["documents"][0][j] if results["documents"] else ""))
        
        if len(cluster) > 1:
            clusters.append(cluster)
            for cid, _, _ in cluster:
                clustered.add(cid)
    
    return clusters


def merge_cluster(client, cluster, dry_run=False):
    """
    Merge a cluster of near-duplicate entries.
    - Keep the entry with most reproductions (or longest content)
    - Sum reproduction counts
    - Delete the rest
    """
    coll = client.get_collection("web_cache")
    
    # Sort: highest reproductions first, then longest content
    def sort_key(item):
        eid, dist, doc = item
        meta = next((m for mid, _, m in all_entries if mid == eid), {})
        repro = int(meta.get("reproductions", 0))
        return (repro, len(doc))
    
    cluster.sort(key=sort_key, reverse=True)
    keeper_id, keeper_dist, keeper_doc = cluster[0]
    duplicate_ids = [cid for cid, _, _ in cluster[1:]]
    
    # Sum reproductions
    total_repro = 0
    all_entries = get_all_entries(client, "web_cache")
    all_meta = {eid: meta for eid, _, meta in all_entries}
    
    for cid, _, _ in cluster:
        meta = all_meta.get(cid, {})
        total_repro += int(meta.get("reproductions", 0))
    
    if not dry_run:
        # Update keeper
        keeper_meta = all_meta.get(keeper_id, {})
        keeper_meta["reproductions"] = str(total_repro)
        if total_repro >= 3:
            keeper_meta["verification"] = "verified"
        coll.update(ids=[keeper_id], metadatas=[keeper_meta])
        
        # Delete duplicates
        if duplicate_ids:
            coll.delete(ids=duplicate_ids)
    
    return {
        "keeper": keeper_id[:12],
        "duplicates_removed": len(duplicate_ids),
        "total_reproductions": total_repro,
        "merged": duplicate_ids,
    }


def run_curation(threshold=0.15, dry_run=False):
    """Main curation pass."""
    print(f"\n🧹 Bot Collective Curator — threshold={threshold}")
    print("=" * 55)
    
    client = connect()
    entries = get_all_entries(client, "web_cache")
    
    if len(entries) < 2:
        print(f"  📦 {len(entries)} entries — nothing to deduplicate")
        return
    
    print(f"  📦 Scanning {len(entries)} entries for near-duplicates...")
    clusters = find_clusters(entries, threshold)
    
    if not clusters:
        print("  ✅ No near-duplicates found")
        return
    
    print(f"  🔍 Found {len(clusters)} cluster(s) with duplicates\n")
    
    total_merged = 0
    for i, cluster in enumerate(clusters):
        result = merge_cluster(client, cluster, dry_run)
        total_merged += result["duplicates_removed"]
        
        print(f"  Cluster {i+1}: keeper={result['keeper']}, "
              f"removed={result['duplicates_removed']} duplicates, "
              f"repro={result['total_reproductions']}")
    
    action = "Would merge" if dry_run else "Merged"
    print(f"\n  📊 {action} {total_merged} duplicates across {len(clusters)} clusters")
    
    # Stats after
    remaining = len(get_all_entries(client, "web_cache"))
    print(f"  📦 Pool: {remaining} entries (was {len(entries)})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot Collective Curator")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument("--threshold", type=float, default=0.15, help="Distance threshold (lower=stricter)")
    args = parser.parse_args()
    
    try:
        run_curation(args.threshold, args.dry_run)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
