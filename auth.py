#!/usr/bin/env python3
"""
Bot Collective Auth — API Key Management & Trust Scoring.
Simple SQLite-backed auth. No passwords, just API keys.
"""

import hashlib
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / 'auth.db'


def _get_conn() -> sqlite3.Connection:
    """Get SQLite connection with WAL mode for concurrent access."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id TEXT PRIMARY KEY,
            api_key_hash TEXT UNIQUE NOT NULL,
            email TEXT DEFAULT '',
            tier TEXT NOT NULL DEFAULT 'free',
            contribution_count INTEGER DEFAULT 0,
            search_count INTEGER DEFAULT 0,
            trust_score REAL DEFAULT 0.1,
            created_at REAL NOT NULL,
            last_active REAL NOT NULL,
            label TEXT DEFAULT ''
        )
    """)
    # Add email column if missing (migration from old schema)
    try:
        conn.execute("ALTER TABLE api_keys ADD COLUMN email TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contribution_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id TEXT NOT NULL,
            source_url TEXT,
            timestamp REAL NOT NULL,
            FOREIGN KEY (key_id) REFERENCES api_keys(key_id)
        )
    """)
    conn.commit()
    conn.close()


# ── Key Generation ──

def generate_api_key(email: str = '', label: str = '', tier: str = 'free') -> str:
    """
    Generate a new API key. Returns the raw key (show once!).
    Format: bc_XXXXXXXXXXXX (32 hex chars)
    """
    raw = 'bc_' + secrets.token_hex(16)
    _store_key(raw, email, tier, label)
    return raw


def _store_key(raw_key: str, email: str = '', tier: str = 'free', label: str = ''):
    """Hash and store a key. Internal — use generate_api_key()."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_id = raw_key[:12]  # First 12 chars as ID
    now = time.time()
    
    conn = _get_conn()
    conn.execute(
        "INSERT INTO api_keys (key_id, api_key_hash, email, tier, created_at, last_active, label) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (key_id, key_hash, email.strip().lower(), tier, now, now, label)
    )
    conn.commit()
    conn.close()


def get_key_by_email(email: str) -> dict | None:
    """Look up API key by email. Returns key info dict or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM api_keys WHERE email = ? ORDER BY created_at DESC LIMIT 1",
        (email.strip().lower(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def email_has_key(email: str) -> bool:
    """Check if email already has a key."""
    return get_key_by_email(email) is not None


# ── Key Verification ──

def verify_api_key(raw_key: str) -> dict | None:
    """
    Verify an API key. Returns key info dict if valid, None if invalid.
    Also updates last_active timestamp.
    """
    if not raw_key or not raw_key.startswith('bc_'):
        return None
    
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM api_keys WHERE api_key_hash = ?",
        (key_hash,)
    ).fetchone()
    
    if row is None:
        conn.close()
        return None
    
    # Update last_active
    conn.execute(
        "UPDATE api_keys SET last_active = ? WHERE key_id = ?",
        (time.time(), row['key_id'])
    )
    conn.commit()
    conn.close()
    
    return dict(row)


# ── Trust Scoring ──

def record_contribution_by_id(key_id: str, source_url: str = '') -> bool:
    """
    Record contribution by key_id (no raw key needed).
    Used by server middleware after auth already verified.
    """
    conn = _get_conn()
    row = conn.execute("SELECT * FROM api_keys WHERE key_id = ?", (key_id,)).fetchone()
    if not row:
        conn.close()
        return False
    
    conn.execute(
        "UPDATE api_keys SET contribution_count = contribution_count + 1, last_active = ? WHERE key_id = ?",
        (time.time(), key_id)
    )
    conn.execute(
        "INSERT INTO contribution_log (key_id, source_url, timestamp) VALUES (?, ?, ?)",
        (key_id, source_url, time.time())
    )
    conn.commit()
    
    # Recalculate trust
    new_count = row['contribution_count'] + 1
    trust = min(1.0, 0.1 + new_count * 0.01)
    conn.execute("UPDATE api_keys SET trust_score = ? WHERE key_id = ?", (trust, key_id))
    conn.commit()
    conn.close()
    return True


def record_search_by_id(key_id: str) -> bool:
    """Record a search by key_id (no raw key needed)."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM api_keys WHERE key_id = ?", (key_id,)).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute(
        "UPDATE api_keys SET search_count = search_count + 1, last_active = ? WHERE key_id = ?",
        (time.time(), key_id)
    )
    conn.commit()
    conn.close()
    return True


def get_trust_score(raw_key: str) -> float:
    """Get trust score for a key. Returns 0.0 for unknown keys."""
    info = verify_api_key(raw_key)
    return info['trust_score'] if info else 0.0


# ── Admin ──

def list_keys() -> list[dict]:
    """List all API keys (admin)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key_id, tier, contribution_count, search_count, trust_score, label, last_active FROM api_keys ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(raw_key: str) -> dict:
    """Get stats for a specific key."""
    info = verify_api_key(raw_key)
    if not info:
        return {'error': 'invalid key'}
    return {
        'key_id': info['key_id'],
        'tier': info['tier'],
        'contributions': info['contribution_count'],
        'searches': info['search_count'],
        'trust_score': info['trust_score'],
        'label': info['label']
    }


def ensure_system_key() -> str:
    """Ensure the system key exists (for bridge watcher). Returns the key."""
    conn = _get_conn()
    row = conn.execute("SELECT api_key_hash FROM api_keys WHERE label = 'system_bridge'").fetchone()
    conn.close()
    if row:
        return 'bc_system_bridge'  # Placeholder - actual key stored in env
    
    # Generate a system key
    raw = generate_api_key(label='system_bridge', tier='pro')
    # Set trust to max
    conn = _get_conn()
    conn.execute("UPDATE api_keys SET trust_score = 1.0 WHERE label = 'system_bridge'")
    conn.commit()
    conn.close()
    return raw


# ── Init ──
init_db()


# ══════════════════════════════════════════════════════════════
#  VERIFICATION CODES
# ══════════════════════════════════════════════════════════════

def init_verification_table():
    """Create verification_codes table."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS verification_codes (
            email TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            created_at REAL NOT NULL,
            attempts INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def create_verification_code(email: str) -> str:
    """Generate a 6-digit code for email. Stores it with 10-min expiry."""
    import random
    code = f"{random.randint(0, 999999):06d}"
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO verification_codes (email, code, created_at, attempts) VALUES (?, ?, ?, 0)",
        (email.strip().lower(), code, time.time())
    )
    conn.commit()
    conn.close()
    return code


def verify_code(email: str, code: str) -> bool:
    """Verify a verification code. Max 3 attempts, 10-min expiry."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT code, created_at, attempts FROM verification_codes WHERE email = ?",
        (email.strip().lower(),)
    ).fetchone()
    conn.close()

    if not row:
        return False

    # Check expiry (10 minutes)
    if time.time() - row['created_at'] > 600:
        return False

    # Check attempts
    if row['attempts'] >= 3:
        return False

    # Increment attempts
    conn = _get_conn()
    conn.execute(
        "UPDATE verification_codes SET attempts = attempts + 1 WHERE email = ?",
        (email.strip().lower(),)
    )
    conn.commit()
    conn.close()

    return row['code'] == code.strip()


def delete_verification_code(email: str):
    """Clean up after successful verification."""
    conn = _get_conn()
    conn.execute("DELETE FROM verification_codes WHERE email = ?", (email.strip().lower(),))
    conn.commit()
    conn.close()


init_verification_table()


# ── CLI ──
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Bot Collective Auth Manager')
    sub = parser.add_subparsers(dest='cmd')
    
    sub.add_parser('init', help='Initialize database')
    
    gen = sub.add_parser('generate', help='Generate new API key')
    gen.add_argument('--label', default='', help='Key label')
    gen.add_argument('--tier', default='free', choices=['free', 'pro'])
    
    sub.add_parser('list', help='List all API keys')
    
    verify = sub.add_parser('verify', help='Verify an API key')
    verify.add_argument('key', help='API key to verify')
    
    stats = sub.add_parser('stats', help='Get stats for a key')
    stats.add_argument('key', help='API key')
    
    args = parser.parse_args()
    
    if args.cmd == 'init':
        init_db()
        print("✅ Database initialized")
    elif args.cmd == 'generate':
        key = generate_api_key(label=args.label, tier=args.tier)
        print(f"🔑 API Key: {key}")
        print(f"   Tier: {args.tier}")
        print(f"   ⚠️  Save this key! It won't be shown again.")
    elif args.cmd == 'list':
        for k in list_keys():
            print(f"  {k['key_id']} | {k['tier']:5s} | trust={k['trust_score']:.2f} | contrib={k['contribution_count']:4d} | search={k['search_count']:4d} | {k['label']}")
    elif args.cmd == 'verify':
        info = verify_api_key(args.key)
        if info:
            print(f"✅ Valid key: {info['key_id']} (tier={info['tier']}, trust={info['trust_score']:.2f})")
        else:
            print("❌ Invalid key")
    elif args.cmd == 'stats':
        print(get_stats(args.key))
