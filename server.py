"""
FastAPI Cache Server for AgentHive.
REST API for contributing and retrieving cached knowledge.
"""

from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from chroma_manager import ChromaManager
from classifier import (
    ContentClass,
    classify_tool_output,
    is_safe_to_share,
    strip_pii,
)
from capture_receiver import strip_pii_server
from relevance import check_relevance, rank_hits_by_relevance
from auth import (
    verify_api_key, record_contribution_by_id, record_search_by_id,
    get_trust_score, generate_api_key, email_has_key, init_db as auth_init,
    create_verification_code, verify_code, delete_verification_code,
)
from rate_limit import check_search_limit, check_contribute_limit, check_signup_limit

from fastapi import Header, Depends

# ── GLOBALS ───────────────────────────────────────────────────

import os

db: Optional[ChromaManager] = None
SYSTEM_KEY = os.getenv("BC_SYSTEM_KEY", "bc_system_bridge_localdev")
# WARNING: Change BC_SYSTEM_KEY in production. Default is for local dev only.


# ── AUTH DEPENDENCY ──────────────────────────────────────────

def require_api_key(x_api_key: str = Header(None, alias="X-API-Key")) -> dict:
    """
    FastAPI dependency: verify X-API-Key header.
    Returns key info dict or raises 401.
    """
    # Allow system key without verification
    if x_api_key == SYSTEM_KEY:
        return {"key_id": "system", "tier": "pro", "trust_score": 1.0, "bypass": True}
    
    info = verify_api_key(x_api_key or "")
    if not info:
        raise HTTPException(status_code=401, detail="Invalid or missing API key. Get one at https://bot-collective.dev")
    return info


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    db = ChromaManager()
    print(f"📦 ChromaDB ready: {db.get_stats()}")
    yield


app = FastAPI(
    title="AgentHive Cache",
    description="Shared knowledge mesh for AI agents",
    version="0.1.0",
    lifespan=lifespan,
)

# Static files (logo, etc.)
import os as _os
_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
_os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# CORS — allow web signup + API access from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── RATE LIMIT MIDDLEWARE ────────────────────────────────────

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting to API endpoints based on X-API-Key header."""
    path = request.url.path
    
    # Skip rate limiting for static pages and health
    if path in ("/", "/signup", "/tos", "/privacy", "/health", "/docs", "/openapi.json"):
        return await call_next(request)
    
    # Get auth info from header
    api_key = request.headers.get("X-API-Key", "")
    auth_info = verify_api_key(api_key) if api_key.startswith("bc_") else None
    client_ip = request.client.host if request.client else "unknown"
    
    # Apply rate limits per endpoint type
    if "/search" in path:
        allowed, remaining = check_search_limit(auth_info or {}, client_ip)
    elif "/contribute" in path or "/bridge-capture" in path:
        allowed, remaining = check_contribute_limit(auth_info or {}, client_ip)
    else:
        return await call_next(request)
    
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "message": f"Rate limit exceeded. Try again in a few seconds. Remaining: {remaining:.0f}",
                "tier": auth_info.get('tier', 'free') if auth_info else 'free',
            }
        )
    
    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(int(remaining))
    return response


# ── MODELS ────────────────────────────────────────────────────

class ContributeRequest(BaseModel):
    query: str
    content: str
    source_url: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy_class: str = "public"
    user_level: int = Field(default=1, ge=0, le=3)
    tool_name: str = "web_search"
    resolve_action: str = ""   # "" | "update" | "keep_both"
    resolve_id: str = ""        # target entry ID for "update"


class ContributeResponse(BaseModel):
    id: str
    classification: str
    pii_stripped: bool
    contributed: bool
    conflicts: list[dict] = []   # potential duplicate entries
    needs_review: bool = False   # True if bot should review conflicts


class SearchResponse(BaseModel):
    query: str
    hits: list[dict]
    count: int
    from_cache: bool
    relevance_summary: dict = Field(default_factory=dict)


class StatsResponse(BaseModel):
    web_cache: int = 0
    skills_library: int = 0
    verified_solutions: int = 0


# ── ENDPOINTS ─────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "bot-collective-cache"}


@app.get("/stats", response_model=StatsResponse)
async def stats():
    """Return collection statistics."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")
    raw = db.get_stats()
    # Default missing keys
    return StatsResponse(
        web_cache=raw.get("web_cache", 0),
        skills_library=raw.get("skills_library", 0),
        verified_solutions=raw.get("verified_solutions", 0),
    )


@app.post("/contribute", response_model=ContributeResponse)
async def contribute(
    req: ContributeRequest,
    file_path: str = Query(default=""),
    auth: dict = Depends(require_api_key),
):
    """
    Contribute a web search result to the shared cache.
    Server-side classification and PII stripping.
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    # Server-side safety checks
    can_share, classification, _ = is_safe_to_share(
        tool_name=req.tool_name,
        content=req.content,
        file_path=file_path,
        user_level=req.user_level,
    )

    if not can_share:
        return ContributeResponse(
            id="",
            classification=classification,
            pii_stripped=False,
            contributed=False,
        )

    # Strip PII
    clean_content, had_pii = strip_pii(req.content)

    # ── Data Quality Validation ──
    validation_errors = validate_contribution(req, clean_content)
    if validation_errors:
        return ContributeResponse(
            id="",
            classification="rejected_validation",
            pii_stripped=had_pii,
            contributed=False,
        )

    # Normalize tags to canonical form
    normalized_tags = normalize_tags(req.tags)
    
    # Auto-fill empty source_url for web_cache
    source_url = req.source_url.strip() if req.source_url else ""

    # ── Handle resolve actions ──
    if req.resolve_action in ("update", "keep_both"):
        try:
            item_id = db.contribute_web_result(
                query=req.query.strip(),
                content=clean_content,
                source_url=source_url,
                tags=normalized_tags,
                privacy_class=req.privacy_class,
                resolve_action=req.resolve_action,
                target_id=req.resolve_id.strip(),
            )
        except ValueError as e:
            return ContributeResponse(
                id="",
                classification="error",
                pii_stripped=had_pii,
                contributed=False,
            )
        return ContributeResponse(
            id=item_id,
            classification=classification,
            pii_stripped=had_pii,
            contributed=True,
            conflicts=post_resolve_conflicts(req, db, clean_content),
        )

    # ── Conflict detection (lightweight, server-side only) ──
    conflicts = db.detect_conflicts(query=req.query.strip(), content=clean_content)
    if conflicts:
        return ContributeResponse(
            id="",
            classification="needs_review",
            pii_stripped=had_pii,
            contributed=False,
            conflicts=conflicts,
            needs_review=True,
        )

    # ── No conflicts → contribute normally ──
    item_id = db.contribute_web_result(
        query=req.query.strip(),
        content=clean_content,
        source_url=source_url,
        tags=normalized_tags,
        privacy_class=req.privacy_class,
    )

    # Record contribution for trust scoring
    if not auth.get('bypass'):
        record_contribution_by_id(auth.get('key_id', ''), source_url)

    return ContributeResponse(
        id=item_id,
        classification=classification,
        pii_stripped=had_pii,
        contributed=True,
    )


@app.post("/contribute/skill", response_model=ContributeResponse)
async def contribute_skill(req: ContributeRequest):
    """Contribute a skill template to the shared library."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    if req.user_level < 2:
        return ContributeResponse(
            id="",
            classification="insufficient_level",
            pii_stripped=False,
            contributed=False,
        )

    item_id = db.contribute_skill(
        name=req.query, content=req.content, tags=req.tags
    )

    return ContributeResponse(
        id=item_id,
        classification="skill",
        pii_stripped=False,
        contributed=True,
    )


# ── DATA QUALITY VALIDATION ─────────────────────────────────

# Canonical tag list — contributions MUST use these (or mapped to them)
CANONICAL_TAGS = {
    # Domain tags
    "welfare": ["welfare", "disability", "swd", "cssa", "allowance", "津貼", "社署", "傷殘"],
    "tax": ["tax", "ird", "inland-revenue", "稅", "稅務"],
    "education": ["education", "school", "edb", "學校", "教育", "小一", "升學"],
    "medical": ["medical", "health", "clinical", "asd", "adhd", "醫療", "自閉"],
    "law": ["law", "legal", "ordinance", "法例", "cap", "條例"],
    "finance": ["finance", "stock", "investment", "港股", "美股", "投資"],
    "policy": ["policy", "government", "circular", "政策"],
    "housing": ["housing", "property", "mortgage", "樓", "房屋"],
    "immigration": ["immigration", "visa", "passport", "移民"],
    
    # Technical tags
    "tech-dev": ["godot", "python", "api", "code", "programming", "dev"],
    "tech-creative": ["zbrush", "blender", "3d-print", "design", "stl"],
    "ai-ml": ["ai", "ml", "llm", "machine-learning", "deep-learning"],
    
    # Geo tags
    "hong-kong": ["hong-kong", "hk", "香港"],
    "china": ["china", "mainland", "china", "大陸", "內地"],
    "international": ["international", "global", "overseas"],
    
    # Meta
    "temporary": ["temporary", "covid", "pilot", "臨時", "特別安排"],
    "evergreen": ["evergreen", "fundamental", "basics"],
}

# Reverse mapping: any variant → canonical
TAG_ALIASES: dict[str, str] = {}
for canonical, aliases in CANONICAL_TAGS.items():
    TAG_ALIASES[canonical.lower()] = canonical
    for alias in aliases:
        TAG_ALIASES[alias.lower()] = canonical


def normalize_tags(tags: list[str]) -> list[str]:
    """Map user-provided tags to canonical tags. Returns deduplicated canonical list."""
    canonical_set = set()
    for tag in tags:
        tag_lower = tag.strip().lower()
        if tag_lower in TAG_ALIASES:
            canonical_set.add(TAG_ALIASES[tag_lower])
        elif tag_lower in CANONICAL_TAGS:
            canonical_set.add(tag_lower)
        # Unknown tags are silently dropped (not added)
    return sorted(canonical_set)


def post_resolve_conflicts(req, db, clean_content: str) -> list[dict]:
    """After an update, check if new content conflicts with OTHER entries (not the target)."""
    if req.resolve_action != "update" or not req.resolve_id:
        return []
    all_conflicts = db.detect_conflicts(query=req.query.strip(), content=clean_content)
    return [c for c in all_conflicts if c["id"] != req.resolve_id.strip()]


def validate_contribution(req, clean_content: str) -> list[str]:
    """Validate a contribution. Returns list of error messages (empty = ok)."""
    errors = []
    
    # Content quality
    content_len = len(clean_content.strip())
    if content_len < 50:
        errors.append(f"Content too short ({content_len} chars, min 50)")
    if content_len > 8000:
        errors.append(f"Content too long ({content_len} chars, max 8000)")
    
    # Query quality
    query = req.query.strip()
    if len(query) < 3:
        errors.append("Query too short")
    if len(query) > 500:
        errors.append("Query too long")
    
    # Source URL required for web_cache
    if req.tool_name == "web_search" and not req.source_url.strip():
        errors.append("source_url is required for web_search contributions")
    
    # Tags: must have at least 2 that map to canonical
    normalized = normalize_tags(req.tags)
    if len(normalized) < 2:
        errors.append(f"Need at least 2 canonical tags (got {len(normalized)}: {normalized})")
    
    return errors


# ── TRUST SCORING ──────────────────────────────────────────

# Domain decay periods in days (how fast freshness decays per domain)
DOMAIN_DECAY = {
    "finance": 1,        # Stock prices
    "policy": 90,        # Government circulars
    "tax": 365,          # Annual budget changes
    "welfare": 730,      # 2-3 year policy cycles
    "education": 365,    # Annual admission cycles
    "law": 1095,         # 3-5 year ordinance changes
    "medical": 730,      # Clinical guidelines
    "tech-creative": 1460,  # 3D printing, design
    "tech-dev": 365,     # API versions
    "evergreen": 365000, # Basically never
}
DEFAULT_DECAY = 180  # 6 months for unknown domains

# Source authority by domain
def source_authority(url: str) -> float:
    if ".gov.hk" in url or ".gov" in url:
        return 1.0
    if ".edu" in url:
        return 0.8
    if any(n in url for n in ["who.int", "un.org", "legislation.gov.hk"]):
        return 0.9
    if any(n in url for n in ["heephong.org", "sen.org", "swd.gov.hk", "edb.gov.hk"]):
        return 0.85
    if any(n in url for n in ["wikipedia.org"]):
        return 0.7
    if any(n in url for n in ["medium.com", "blog", "forum", "reddit"]):
        return 0.3
    return 0.5  # Unknown


def infer_domain(tags: list[str]) -> str:
    """Infer content domain from tags for decay calculation."""
    tag_lower = " ".join(tags).lower()
    domain_map = {
        "tax": ["tax", "稅", "ird", "inland"],
        "finance": ["stock", "股息", "price", "匯率"],
        "welfare": ["disability", "傷殘", "swd", "cssa", "allowance"],
        "education": ["school", "education", "學校", "edb", "小一", "p1"],
        "medical": ["medical", "asd", "adhd", "clinical", "treatment"],
        "law": ["law", "法例", "ordinance", "cap", "條例"],
        "tech-dev": ["godot", "python", "api", "programming"],
        "tech-creative": ["zbrush", "blender", "3d print", "stl"],
    }
    for domain, keywords in domain_map.items():
        if any(kw in tag_lower for kw in keywords):
            return domain
    return "unknown"


# Conservative fallback: when we can't determine domain, assume it decays fast
# Better to underestimate freshness than overestimate it
UNKNOWN_DOMAIN_DECAY = 90  # 3 months for truly unknown content


def calculate_trust(
    reproductions: int,
    source_url: str,
    created_ts: str,
    tags: list[str],
) -> dict:
    """Calculate trust score for a cache entry."""
    try:
        now = __import__("time").time()
        age_days = (now - int(created_ts)) / 86400
    except (ValueError, TypeError):
        age_days = 365

    domain = infer_domain(tags)
    decay_period = DOMAIN_DECAY.get(domain, UNKNOWN_DOMAIN_DECAY)
    freshness = max(1.0 - (age_days / decay_period), 0.0)
    repro_weight = min(int(reproductions) / 5, 1.0)
    authority = source_authority(source_url)

    trust = (repro_weight * 0.4) + (authority * 0.3) + (freshness * 0.3)

    if trust >= 0.85:
        level = "high"
    elif trust >= 0.50:
        level = "medium"
    elif trust >= 0.25:
        level = "low"
    else:
        level = "stale"

    warning = None
    if freshness < 0.3:
        domain_names = {
            "tax": "Tax rates typically change annually (Budget Day).",
            "welfare": "Welfare policies update every 2-3 years.",
            "education": "Education policies follow annual cycles.",
            "medical": "Clinical guidelines may have been updated.",
            "law": "Ordinances may have been amended.",
            "finance": "Financial data is time-sensitive.",
        }
        ctx = domain_names.get(domain, "This information may be outdated.")
        warning = f"⚠️ {int(age_days)} days old. {ctx} Consider checking current sources."

    return {
        "verification": "verified" if int(reproductions) >= 3 else "unverified",
        "reproductions": int(reproductions),
        "created": created_ts,
        "freshness_score": round(freshness, 2),
        "trust_score": round(trust, 2),
        "trust_level": level,
        "stale_warning": warning,
    }


# ── SEARCH ENDPOINT ────────────────────────────────────────

@app.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query"),
    n: int = Query(default=3, ge=1, le=10),
    min_sim: float = Query(default=0.4, ge=0.0, le=1.0),
    collection: str = Query(default="web_cache", description="web_cache, skills, solutions, or all"),
    auth: dict = Depends(require_api_key),
):
    """
    Semantic search across the knowledge mesh.
    Returns ranked results with similarity scores.
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    if collection == "web_cache":
        hits = db.search_web_cache(q, n_results=n, min_similarity=min_sim)
    elif collection == "skills":
        # Skills search via all collections, filtered
        all_hits = db.search_all(q, n_results=n)
        hits = [h for h in all_hits if h["collection"] == "skills_library"][:n]
    elif collection == "solutions":
        all_hits = db.search_all(q, n_results=n)
        hits = [h for h in all_hits if h["collection"] == "verified_solutions"][:n]
    else:
        hits = db.search_all(q, n_results=n)

    # Enrich hits with trust scores
    for hit in hits:
        tags = hit.get("tags", "")
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        trust = calculate_trust(
            reproductions=hit.get("reproductions", 0),
            source_url=hit.get("source_url", ""),
            created_ts=hit.get("created", "0"),
            tags=tag_list,
        )
        hit.update(trust)

    # ── Relevance Check Layer ──
    # Add relevance scoring and re-rank: DIRECT_MATCH first, TOPIC_ONLY last
    hits = rank_hits_by_relevance(q, hits)

    # Build summary for bot decision-making
    direct = sum(1 for h in hits if h.get("relevance") == "DIRECT_MATCH")
    narrow = sum(1 for h in hits if h.get("relevance") == "NARROWS_DOWN")
    topic_only = sum(1 for h in hits if h.get("relevance") == "TOPIC_ONLY")

    if direct > 0:
        action = "present_answer"
        guidance = "Cache contains likely answer. Present to user with confidence."
    elif narrow > 0:
        action = "use_as_context"
        guidance = "Cache narrows domain. Use as search context, then targeted web search for specific answer."
    elif topic_only > 0:
        action = "discard_or_weak_context"
        guidance = "Same topic but irrelevant to query. Consider discarding or use only as last-resort context."
    else:
        action = "no_cache"
        guidance = "No relevant cache entries. Full web search needed."

    relevance_summary = {
        "action": action,
        "guidance": guidance,
        "breakdown": {
            "direct_match": direct,
            "narrows_down": narrow,
            "topic_only": topic_only,
        },
    }

    # Record search for analytics
    if not auth.get('bypass'):
        record_search_by_id(auth.get('key_id', ''))

    return SearchResponse(
        query=q,
        hits=hits,
        count=len(hits),
        from_cache=len(hits) > 0,
        relevance_summary=relevance_summary,
    )


@app.post("/admin/expire")
async def expire():
    """Remove expired entries. Admin endpoint."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")
    count = db.expire_old_entries()
    return {"expired_count": count}


# ── HUMAN BRIDGE CAPTURE ENDPOINT ────────────────────────────

class BridgeCaptureRequest(BaseModel):
    query: str = ""
    content: str = ""
    source_url: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy_class: str = "public"
    user_level: int = Field(default=2, ge=0, le=3)
    tool_name: str = "human_bridge_capture"


@app.post("/bcp/v1/bridge-capture")
async def bridge_capture(req: BridgeCaptureRequest, auth: dict = Depends(require_api_key)):
    """
    Layer 3 PII re-filter endpoint for Human Bridge captures.
    Receives pre-screened captures, does aggressive server-side PII scan,
    then contributes to ChromaDB.
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    # ── Layer 3: Server-side PII re-scan ──
    clean_content, pii_stripped = strip_pii_server(req.content)

    # ── Quality check ──
    if not clean_content or len(clean_content.strip()) < 50:
        return {
            "contributed": False,
            "pii_stripped": pii_stripped,
            "quality_pass": False,
            "reason": "Content too short or empty after PII strip",
        }

    # ── Conflict detection (lightweight) ──
    conflicts = db.detect_conflicts(
        query=req.query.strip() or req.source_url,
        content=clean_content,
    )
    
    # ── Contribute to ChromaDB ──
    try:
        item_id = db.contribute_web_result(
            query=req.query.strip() or req.source_url,
            content=clean_content,
            source_url=req.source_url,
            tags=req.tags + ["human-bridge"],
            privacy_class=req.privacy_class,
            resolve_action="keep_both" if conflicts else "",  # Auto-keep_both on conflict, never silent merge
        )
    except Exception as e:
        return {
            "contributed": False,
            "pii_stripped": pii_stripped,
            "quality_pass": True,
            "reason": f"ChromaDB error: {str(e)}",
        }

    return {
        "contributed": True,
        "item_id": item_id,
        "pii_stripped": pii_stripped,
        "pii_stripped_count": len(pii_stripped),
        "quality_pass": True,
        "conflicts_found": len(conflicts),
        "conflicts": conflicts,
        "note": "Layer 3 PII scan complete" + (f", {len(pii_stripped)} patterns stripped" if pii_stripped else ", clean") + (f", {len(conflicts)} conflict(s) auto-resolved with keep_both" if conflicts else ", no conflicts"),
    }


# ══════════════════════════════════════════════════════════════
#  LANDING PAGE & SIGNUP
# ══════════════════════════════════════════════════════════════

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentHive — Shared Knowledge for AI Agents</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; line-height: 1.6; }
  .container { max-width: 800px; margin: 0 auto; padding: 2rem 1.5rem; }
  nav { display: flex; justify-content: space-between; align-items: center; padding: 1.5rem 0; border-bottom: 1px solid #1e1e2e; }
  .logo { font-size: 1.4rem; font-weight: 700; color: #7c3aed; }
  nav a { color: #888; text-decoration: none; margin-left: 1.5rem; font-size: 0.9rem; }
  nav a:hover { color: #c4b5fd; }
  .hero { text-align: center; padding: 4rem 0 3rem; }
  .hero h1 { font-size: 2.8rem; font-weight: 800; background: linear-gradient(135deg, #7c3aed, #a78bfa, #c4b5fd); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 1rem; }
  .hero p { font-size: 1.2rem; color: #999; max-width: 550px; margin: 0 auto 2rem; }
  .badge { display: inline-block; background: #1e1e2e; color: #7c3aed; padding: 0.3rem 1rem; border-radius: 20px; font-size: 0.85rem; margin-bottom: 1rem; border: 1px solid #2e2e3e; }
  .signup-box { background: #13131a; border: 1px solid #1e1e2e; border-radius: 12px; padding: 2.5rem; max-width: 480px; margin: 0 auto; }
  .signup-box h2 { font-size: 1.3rem; margin-bottom: 1.5rem; color: #c4b5fd; }
  .form-group { margin-bottom: 1.2rem; text-align: left; }
  .form-group label { display: block; font-size: 0.85rem; color: #888; margin-bottom: 0.4rem; }
  .form-group input { width: 100%; padding: 0.75rem 1rem; background: #0a0a0f; border: 1px solid #2e2e3e; border-radius: 8px; color: #e0e0e0; font-size: 1rem; }
  .form-group input:focus { outline: none; border-color: #7c3aed; }
  .btn { display: inline-block; width: 100%; padding: 0.85rem; background: #7c3aed; color: white; border: none; border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; }
  .btn:hover { background: #6d28d9; }
  .btn:disabled { background: #3b3b4e; cursor: not-allowed; }
  .result { margin-top: 1rem; padding: 1rem; background: #0f0f1a; border-radius: 8px; border: 1px solid #2e2e3e; display: none; }
  .result.success { border-color: #22c55e; display: block; }
  .result.error { border-color: #ef4444; display: block; }
  .result .key { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 1.1rem; color: #22c55e; word-break: break-all; padding: 0.5rem 0; }
  .result .warn { color: #f59e0b; font-size: 0.85rem; margin-top: 0.5rem; }
  .features { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.5rem; padding: 3rem 0; }
  .feature { background: #13131a; border: 1px solid #1e1e2e; border-radius: 10px; padding: 1.5rem; }
  .feature .icon { font-size: 1.8rem; margin-bottom: 0.8rem; }
  .feature h3 { font-size: 1rem; margin-bottom: 0.5rem; color: #c4b5fd; }
  .feature p { font-size: 0.9rem; color: #888; }
  .pricing { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; padding: 2rem 0 4rem; }
  .plan { background: #13131a; border: 1px solid #1e1e2e; border-radius: 10px; padding: 2rem 1.5rem; text-align: center; }
  .plan.pro { border-color: #7c3aed; background: #13131f; }
  .plan h3 { font-size: 1.1rem; color: #c4b5fd; margin-bottom: 0.5rem; }
  .plan .price { font-size: 2rem; font-weight: 800; margin: 0.8rem 0; }
  .plan .price span { font-size: 0.9rem; color: #888; font-weight: 400; }
  .plan ul { list-style: none; text-align: left; font-size: 0.85rem; color: #999; margin: 1rem 0; }
  .plan ul li { padding: 0.3rem 0; }
  .plan ul li::before { content: '✓ '; color: #22c55e; }
  footer { text-align: center; padding: 2rem 0; border-top: 1px solid #1e1e2e; color: #666; font-size: 0.85rem; }
  footer a { color: #7c3aed; text-decoration: none; }
  .stats { text-align: center; padding: 2rem 0; }
  .stats .num { font-size: 2.5rem; font-weight: 800; color: #7c3aed; }
  .stats .label { font-size: 0.85rem; color: #888; }
  @media (max-width: 600px) { .hero h1 { font-size: 2rem; } .hero p { font-size: 1rem; } }
</style>
</head>
<body>
<div class="container">
  <nav>
    <span class="logo">🜁 AgentHive</span>
    <div>
      <a href="/tos">Terms</a>
      <a href="/privacy">Privacy</a>
    </div>
  </nav>

  <section class="hero">
    <span class="badge">🚧 Beta — Free during testing</span>
    <h1>Shared Knowledge<br>for AI Agents</h1>
    <p>Stop burning tokens on the same blocked searches. One agent finds it, every agent remembers it.</p>
  </section>

  <div class="disclaimer" style="background:#1a1a0f;border:1px solid #3e3e1e;border-radius:10px;padding:1rem 1.5rem;margin:0 auto 1.5rem;max-width:720px;text-align:center;">
    <p style="color:#f59e0b;font-size:0.85rem;margin:0;">⚠️ Content is <strong>user-contributed and unverified</strong>. AgentHive makes no guarantees of accuracy, completeness, or timeliness. Independently verify any information used for legal, medical, or financial decisions against official sources.</p>
  </div>

  <div class="signup-box" id="signupBox">
    <h2>🔑 Get Your API Key</h2>
    <form id="signupForm" onsubmit="handleSignup(event)">
      <div class="form-group">
        <label>Email</label>
        <input type="email" id="email" placeholder="you@example.com" required>
      </div>
      <div class="form-group">
        <label>Label (optional)</label>
        <input type="text" id="label" placeholder="e.g. My Hermes Agent">
      </div>
      <button type="submit" class="btn" id="submitBtn">Generate Free API Key</button>
    </form>
    <div class="result" id="result">
      <p style="color:#888;font-size:0.85rem;">Your API key:</p>
      <div class="key" id="apiKey"></div>
      <p class="warn">⚠️ Save this key now! It won't be shown again.</p>
      <p class="warn" style="color:#f59e0b;font-size:0.8rem;margin-top:0.3rem;">你提交嘅查詢內容可能被儲存於共享 cache 並被其他用戶存取。請勿提交個人資料。PII 會被自動過濾但不能保證 100%。</p>
      <p style="color:#888;font-size:0.85rem;margin-top:0.5rem;">Use it in requests: <code style="background:#1e1e2e;padding:2px 6px;border-radius:4px;">curl -H "X-API-Key: YOUR_KEY" ...</code></p>
    </div>
  </div>

  <section class="features">
    <div class="feature">
      <div class="icon">🔍</div>
      <h3>Cache-First Search</h3>
      <p>Before hitting the web, check if another agent already found the answer. Save tokens.</p>
    </div>
    <div class="feature">
      <div class="icon">🛡️</div>
      <h3>Auto PII Stripping</h3>
      <p>Three-layer defense: emails, phones, national IDs stripped before anything leaves your agent.</p>
    </div>
    <div class="feature">
      <div class="icon">🌐</div>
      <h3>Open Protocol</h3>
      <p>BCP v0.1 is open. Any agent can join. Build your own node. Connect to the mesh.</p>
    </div>
    <div class="feature">
      <div class="icon">🤝</div>
      <h3>Trust Scoring</h3>
      <p>Entries verified by multiple agents earn higher trust. See freshness, authority, reproductions.</p>
    </div>
  </section>

  <h2 style="text-align:center;color:#c4b5fd;margin-bottom:1.5rem;">Pricing</h2>
  <div class="pricing">
    <div class="plan">
      <h3>Free</h3>
      <div class="price">$0<span>/mo</span></div>
      <ul>
        <li>60 searches/min</li>
        <li>10 contributes/min</li>
        <li>Basic trust scoring</li>
        <li>Community cache access</li>
      </ul>
    </div>
    <div class="plan pro">
      <h3>Pro</h3>
      <div class="price">$4<span>/mo</span></div>
      <ul>
        <li>Unlimited searches</li>
        <li>Priority queries</li>
        <li>Verified-only filter</li>
        <li>Early access features</li>
      </ul>
    </div>
    <div class="plan">
      <h3>Enterprise</h3>
      <div class="price">Custom</div>
      <ul>
        <li>Self-hosted node</li>
        <li>SLA guarantee</li>
        <li>Private collections</li>
        <li>Dedicated support</li>
      </ul>
    </div>
  </div>

  <footer>
    <p>AgentHive · Open Protocol · <a href="/tos">Terms</a> · <a href="/privacy">Privacy</a></p>
    <p style="margin-top:0.3rem;">Built for agents. Contributed by humans.</p>
  </footer>
</div>
<script>
async function handleSignup(e) {
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  const label = document.getElementById('label').value.trim();
  const btn = document.getElementById('submitBtn');
  const result = document.getElementById('result');
  const apiKeyEl = document.getElementById('apiKey');
  btn.disabled = true;
  btn.textContent = 'Generating...';
  result.className = 'result';
  try {
    const resp = await fetch('/api/keys/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, label})
    });
    const data = await resp.json();
    if (resp.ok) {
      apiKeyEl.textContent = data.api_key;
      result.className = 'result success';
    } else {
      apiKeyEl.textContent = data.detail || 'Unknown error';
      result.className = 'result error';
    }
  } catch (err) {
    apiKeyEl.textContent = 'Network error. Is the server running?';
    result.className = 'result error';
  }
  btn.disabled = false;
  btn.textContent = 'Generate Free API Key';
}
</script>
</body>
</html>"""


TOS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Terms of Service — AgentHive</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; line-height: 1.7; max-width: 720px; margin: 0 auto; padding: 2rem 1.5rem; }
  h1 { color: #7c3aed; font-size: 1.8rem; margin-bottom: 1.5rem; }
  h2 { color: #c4b5fd; font-size: 1.2rem; margin: 2rem 0 0.8rem; }
  p, li { color: #999; }
  a { color: #7c3aed; }
</style>
</head>
<body>
<h1>Terms of Service</h1>
<p><strong>Last updated: June 2026</strong></p>

<h2>1. Acceptance</h2>
<p>By using AgentHive ("the Service"), you agree to these Terms. If you don't agree, don't use it.</p>

<h2>2. The Service</h2>
<p>AgentHive is a shared knowledge cache for AI agents. It stores and retrieves web search results <strong>contributed by users</strong>. The Service is provided "as is" with no guarantees of accuracy, availability, or fitness for any purpose. Content is not verified, fact-checked, or endorsed by AgentHive. Users should independently verify any information used for legal, medical, financial, or other consequential decisions.</p>

<h2>3. Content Sources</h2>
<p>Content in the cache is captured and contributed by users via their AI agents or browser extensions. User-contributed content may include information from websites that are inaccessible to automated bots. AgentHive does not scrape, monitor, or verify these external sources. You are solely responsible for the content you contribute and must ensure you have the right to share it.</p>

<h2>4. API Keys</h2>
<p>You must use a valid API key to access the Service. You are responsible for keeping your key secure. Abuse (excessive requests, spam, malicious content) will result in key revocation without notice.</p>

<h2>5. Content You Contribute</h2>
<p>By contributing content to the cache, you grant AgentHive a perpetual, worldwide, royalty-free license to store, index, and serve that content to other users. You represent that you have the right to share the content and that it does not contain personal information (PII is auto-stripped).</p>

<h2>6. Privacy</h2>
<p>We auto-strip PII (emails, phone numbers, national ID numbers, IP addresses) from all contributed content. See our <a href="/privacy">Privacy Policy</a> for details.</p>

<h2>7. Limitations</h2>
<p>AgentHive is not liable for any damages arising from use of the Service, including but not limited to: incorrect cached information, service downtime, or data loss.</p>

<h2>8. Changes</h2>
<p>We may update these terms at any time. Continued use after changes constitutes acceptance.</p>

<h2>9. Contact</h2>
<p>For questions: file an issue on the GitHub repository.</p>
</body>
</html>"""


PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Privacy Policy — AgentHive</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; line-height: 1.7; max-width: 720px; margin: 0 auto; padding: 2rem 1.5rem; }
  h1 { color: #7c3aed; font-size: 1.8rem; margin-bottom: 1.5rem; }
  h2 { color: #c4b5fd; font-size: 1.2rem; margin: 2rem 0 0.8rem; }
  p, li { color: #999; }
  a { color: #7c3aed; }
</style>
</head>
<body>
<h1>Privacy Policy</h1>
<p><strong>Last updated: June 2026</strong></p>

<h2>What We Collect</h2>
<ul>
  <li><strong>Email address</strong> — when you sign up for an API key. Used only for key management.</li>
  <li><strong>API usage metrics</strong> — search count, contribution count, trust score. Tied to your API key, not your identity.</li>
  <li><strong>Web search results you contribute</strong> — after PII is stripped (see below).</li>
</ul>

<h2>What We DON'T Collect</h2>
<ul>
  <li>Your AI agent's memory, conversations, or private files.</li>
  <li>Browsing history or personal data from your device.</li>
  <li>Passwords — we use API key hashing (SHA-256), not plaintext.</li>
</ul>

<h2>PII Stripping</h2>
<p>All contributed content passes through three layers of PII detection before storage:</p>
<ol>
  <li><strong>Agent-side</strong> — your agent classifies content before sending.</li>
  <li><strong>Extension-side</strong> — Human Bridge extension auto-blocks PII patterns.</li>
  <li><strong>Server-side</strong> — aggressive regex scan strips any remaining PII.</li>
</ol>
<p>Patterns stripped: email addresses, phone numbers (HK, CN, US, UK, JP, TW, SG, KR), national ID numbers (HK, China, US SSN, UK NI, Taiwan, Singapore NRIC, Korea RRN, Brazil CPF plus more), IP addresses, API keys, credit card numbers, and passport numbers.</p>

<h2>Data Storage & Retention</h2>
<p>Cache entries are stored in ChromaDB with creation timestamps. Entries may be expired based on domain-specific decay rules (e.g., financial data expires faster than legal references). You can request deletion of your API key and associated metadata by contacting us.</p>

<h2>Third Parties</h2>
<p>We do not sell, share, or transfer your data to third parties. The cache pool is the <em>product</em> — your data powers the shared knowledge mesh, nothing else.</p>

<h2>Contact</h2>
<p>For privacy concerns: file an issue on the GitHub repository.</p>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def landing():
    """Serve the multi-language landing page from external file."""
    import os, re
    html_path = os.path.join(os.path.dirname(__file__), "landing-v3-i18n.html")
    if not os.path.exists(html_path):
        return HTMLResponse(LANDING_HTML)  # Fallback to old inline
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Strip line-number prefixes (e.g. "     1|") in case file gets corrupted
    content = re.sub(r'^[ \t]*\d+\|', '', content, flags=re.MULTILINE)
    return HTMLResponse(content)


@app.get("/tos", response_class=HTMLResponse)
async def tos():
    return HTMLResponse(TOS_HTML)


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return HTMLResponse(PRIVACY_HTML)


# ══════════════════════════════════════════════════════════════
#  API KEY MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════

class KeyGenerateRequest(BaseModel):
    email: str
    label: str = ""
    code: str = ""  # Verification code (required now)


class KeyGenerateResponse(BaseModel):
    api_key: str
    tier: str
    message: str


class VerificationRequest(BaseModel):
    email: str


class VerificationResponse(BaseModel):
    code: str  # Shown in dev mode
    message: str
    expires_in: int = 600


@app.post("/api/keys/request-verification", response_model=VerificationResponse)
async def request_verification(req: VerificationRequest, request: Request):
    """
    Step 1: Request a verification code for an email.
    Returns the code (shown on page in dev mode).
    """
    email = req.email.strip().lower()
    if not email or "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Valid email required")

    # Rate limit
    client_ip = request.client.host if request.client else "unknown"
    allowed, remaining = check_signup_limit(client_ip)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")

    # Check if already has a key
    if email_has_key(email):
        raise HTTPException(status_code=409, detail="This email already has an API key.")

    code = create_verification_code(email)
    return VerificationResponse(
        code=code,
        message=f"Verification code generated. For development: your code is {code}",
        expires_in=600,
    )


@app.post("/api/keys/generate", response_model=KeyGenerateResponse)
async def generate_key(req: KeyGenerateRequest, request: Request):
    """
    Step 2: Verify code and generate API key.
    """
    email = req.email.strip().lower()
    if not email or "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Valid email required")

    # Require verification code
    if not req.code or not req.code.strip():
        raise HTTPException(status_code=400, detail="Verification code required")

    if not verify_code(email, req.code):
        raise HTTPException(status_code=401, detail="Invalid or expired verification code")

    # Check if email already has a key
    if email_has_key(email):
        raise HTTPException(status_code=409, detail="This email already has an API key.")

    # Generate key
    label = req.label.strip() or email.split("@")[0]
    raw_key = generate_api_key(email=email, label=label, tier="free")

    # Clean up verification code
    delete_verification_code(email)

    return KeyGenerateResponse(
        api_key=raw_key,
        tier="free",
        message="Save this key! It won't be shown again. Use: curl -H 'X-API-Key: YOUR_KEY' ..."
    )


@app.get("/api/keys/stats")
async def key_stats(auth: dict = Depends(require_api_key)):
    """Get usage stats for the authenticated API key."""
    if auth.get("bypass"):
        return {"tier": "system", "note": "System key — no limits"}
    
    from auth import get_stats as auth_stats
    # We have the key_id from auth, need to reconstruct stats
    key_id = auth.get("key_id", "")
    if not key_id:
        raise HTTPException(status_code=401, detail="Invalid key")
    
    return {
        "key_id": key_id,
        "tier": auth.get("tier", "free"),
        "trust_score": auth.get("trust_score", 0.0),
        "note": "Full stats available via /search and /contribute history"
    }


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", "15000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
