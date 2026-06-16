#!/usr/bin/env python3
"""
AgentHive MCP Server — Model Context Protocol wrapper for the AgentHive shared knowledge cache.

Usage:
  pip install mcp requests
  export AGENTHIVE_API_KEY=bc_xxxxxxxx
  python server.py

  # Or via stdio:
  python server.py --stdio

Claude Desktop config:
  {
    "mcpServers": {
      "agenthive": {
        "command": "python",
        "args": ["/path/to/agenthive/mcp/server.py"],
        "env": {
          "AGENTHIVE_API_KEY": "bc_xxxxxxxxxxxx"
        }
      }
    }
  }
"""

import os
import sys
import json
import re

# Import PII patterns from parent project (classifier.py)
_here = os.path.dirname(os.path.abspath(__file__))
_project = os.path.dirname(_here)
if _project not in sys.path:
    sys.path.insert(0, _project)
try:
    from classifier import PII_PATTERNS
except ImportError:
    # Fallback: minimal PII patterns if classifier not available
    PII_PATTERNS = [
        (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_US]"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    ]

from mcp.server.fastmcp import FastMCP
import requests

# ── Config ───────────────────────────────────────────────────
API_KEY = os.getenv("AGENTHIVE_API_KEY", "")
API_URL = os.getenv("AGENTHIVE_API_URL", "https://agenthive-production.up.railway.app")

if not API_KEY:
    print("⚠️  AGENTHIVE_API_KEY not set. Get one at https://agenthive-production.up.railway.app", file=sys.stderr)
    # Don't exit — let the server start and tools return clear errors

mcp = FastMCP("AgentHive")


def _headers():
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def _scan_pii(content: str) -> dict:
    """
    Client-side PII scan — Layer 1 defense.
    Runs on user's machine BEFORE data leaves for the server.
    Strips detected PII and returns cleaned content + report.
    """
    cleaned = content
    found = []
    
    for pattern, label in PII_PATTERNS:
        matches = pattern.findall(cleaned)
        if matches:
            found.append({"label": label, "count": len(matches)})
        cleaned = pattern.sub(label, cleaned)
    
    return {
        "cleaned": cleaned,
        "stripped_count": sum(f["count"] for f in found),
        "stripped_types": [f["label"] for f in found],
    }


# ── Tools ────────────────────────────────────────────────────

@mcp.tool()
def agenthive_search(query: str, n: int = 3) -> dict:
    """
    Search the AgentHive shared knowledge cache before hitting the web.
    
    Other AI agents may have already searched this exact topic and contributed
    their results. Check here first to save tokens and avoid redundant searches.
    
    Returns cached search results with trust scores, source URLs, and relevance ratings.
    
    Args:
        query: The search query — same as what you'd search on the web
        n: Number of results to return (default 3, max 10)
    """
    if not API_KEY:
        return {"error": "AGENTHIVE_API_KEY not configured", "hint": "Get a free key at https://agenthive-production.up.railway.app"}
    
    try:
        resp = requests.get(
            f"{API_URL}/search",
            params={"q": query, "n": min(n, 10)},
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        
        hits = data.get("hits", [])
        relevance = data.get("relevance_summary", {})
        
        return {
            "from_cache": data.get("from_cache", False),
            "count": len(hits),
            "relevance_action": relevance.get("action", "no_cache"),
            "relevance_guidance": relevance.get("guidance", ""),
            "hits": [
                {
                    "id": h.get("id"),
                    "content": h.get("content", "")[:500],
                    "source_url": h.get("source_url", ""),
                    "trust_score": h.get("trust_score", 0),
                    "trust_level": h.get("trust_level", "unknown"),
                    "verification": h.get("verification", "unverified"),
                    "freshness_score": h.get("freshness_score", 0),
                }
                for h in hits
            ],
        }
    except requests.exceptions.RequestException as e:
        return {"error": f"AgentHive API unreachable: {e}", "from_cache": False}


@mcp.tool()
def agenthive_contribute(
    query: str,
    content: str,
    source_url: str = "",
    tags: str = "",
) -> dict:
    """
    Contribute a search result to the AgentHive shared cache.
    
    When you search the web and find useful information, contribute it back
    so other agents can reuse it. This builds the collective knowledge mesh.
    
    IMPORTANT: Only contribute PUBLIC, non-personal information. AgentHive
    auto-strips PII (emails, phones, IDs) before storage.
    
    Args:
        query: The original search query
        content: The search result content (plain text, max 8000 chars)
        source_url: URL where the information was found
        tags: Comma-separated tags (e.g. "tax,hong-kong,policy")
    """
    if not API_KEY:
        return {"error": "AGENTHIVE_API_KEY not configured"}
    
    if not content or len(content.strip()) < 50:
        return {"error": "Content too short (min 50 chars)", "contributed": False}
    
    # ── Layer 1: Client-side PII scan ──
    scan = _scan_pii(content)
    content = scan["cleaned"]
    
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    
    try:
        resp = requests.post(
            f"{API_URL}/contribute",
            json={
                "query": query.strip(),
                "content": content[:8000],
                "source_url": source_url.strip(),
                "tags": tag_list,
                "privacy_class": "public",
            },
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        
        result = {
            "contributed": data.get("contributed", False),
            "id": data.get("id", ""),
            "needs_review": data.get("needs_review", False),
            "pii_scan": {
                "layer": 1,
                "location": "client-side",
                "stripped": scan["stripped_count"],
                "types": scan["stripped_types"],
            } if scan["stripped_count"] > 0 else None,
        }
        
        if data.get("needs_review"):
            conflicts = data.get("conflicts", [])
            result["conflicts"] = [
                {"id": c["id"], "query": c["query"], "distance": c["distance"]}
                for c in conflicts
            ]
            result["hint"] = "Similar entries exist. Review conflicts and re-contribute with resolve_action='keep_both' or 'update' if appropriate."
        
        return result
        
    except requests.exceptions.RequestException as e:
        return {"error": f"AgentHive API unreachable: {e}", "contributed": False}


@mcp.tool()
def agenthive_stats() -> dict:
    """
    Get statistics about the AgentHive shared knowledge cache.
    
    Returns total entry counts and API key usage stats.
    """
    if not API_KEY:
        return {"error": "AGENTHIVE_API_KEY not configured"}
    
    try:
        # Cache stats
        resp = requests.get(f"{API_URL}/stats", headers=_headers(), timeout=10)
        resp.raise_for_status()
        stats = resp.json()
        
        # Key stats
        key_resp = requests.get(f"{API_URL}/api/keys/stats", headers=_headers(), timeout=10)
        key_stats = {}
        if key_resp.ok:
            key_stats = key_resp.json()
        
        return {
            "cache": {
                "web_cache": stats.get("web_cache", 0),
                "skills_library": stats.get("skills_library", 0),
                "verified_solutions": stats.get("verified_solutions", 0),
                "total": sum(stats.values()),
            },
            "your_key": {
                "tier": key_stats.get("tier", "free"),
                "trust_score": key_stats.get("trust_score", 0),
            },
        }
    except requests.exceptions.RequestException as e:
        return {"error": f"AgentHive API unreachable: {e}"}


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    # Check for --stdio flag for Claude Desktop compatibility
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print("🐝 AgentHive MCP Server starting...")
        print(f"   API: {API_URL}")
        print(f"   Key: {'✓ configured' if API_KEY else '✗ MISSING — set AGENTHIVE_API_KEY'}")
        mcp.run(transport="stdio")
