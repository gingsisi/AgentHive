"""
FastAPI Cache Server for AgentHive.
REST API for contributing and retrieving cached knowledge.
# deploy-id: f47ac10b-58cc-4372-a567-0e02b2c3d479-force
"""

from contextlib import asynccontextmanager
from typing import Optional

import re
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
    if path in ("/", "/signup", "/tos", "/privacy", "/agreement", "/health", "/docs", "/openapi.json"):
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
<title>🐝 AgentHive — Agents share. Humans bridge.</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0f;color:#e0e0e0;line-height:1.6}
  .nav{position:sticky;top:0;z-index:100;background:rgba(10,10,15,0.85);backdrop-filter:blur(16px);border-bottom:1px solid #1e1e2e;padding:0 2rem}
  .nav-inner{max-width:1080px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;height:56px}
  .nav-logo{font-size:1.1rem;font-weight:600;color:#fff;text-decoration:none}
  .nav-links{display:flex;gap:1.5rem}
  .nav-links a{font-size:.875rem;color:#e0e0e0;text-decoration:none}
  .nav-links a:hover{color:#533afd}
  .nav-cta{background:#533afd;color:#fff!important;padding:.5rem 1rem;border-radius:4px}
  .container{max-width:1080px;margin:0 auto;padding:2rem}
  .hero{text-align:center;padding:4rem 2rem 3rem}
  .hero h1{font-size:2.8rem;font-weight:800;background:linear-gradient(135deg,#533afd,#a78bfa,#c4b5fd);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:1rem}
  .hero .sub{font-size:1.15rem;color:#999;max-width:580px;margin:0 auto 1rem;line-height:1.6}
  .hero .tagline{font-size:1.3rem;color:#533afd;font-weight:500;margin-bottom:2rem}
  .hero-actions{display:flex;gap:.75rem;justify-content:center;flex-wrap:wrap}
  .btn-primary{font-size:1rem;color:#fff;background:#533afd;padding:.6rem 1.25rem;border:none;border-radius:4px;cursor:pointer;text-decoration:none}
  .btn-primary:hover{background:#4434d4}
  .btn-ghost{font-size:1rem;color:#533afd;background:transparent;padding:.6rem 1.25rem;border:1px solid #b9b9f9;border-radius:4px;cursor:pointer;text-decoration:none}
  .disclaimer{max-width:720px;margin:0 auto 3rem;padding:1rem 1.5rem;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:8px;text-align:center}
  .disclaimer p{font-size:.8125rem;color:#f59e0b}
  .features{max-width:1080px;margin:0 auto;padding:3rem 2rem}
  .features h2{font-size:1.8rem;font-weight:300;color:#e0e0e0;text-align:center;margin-bottom:2.5rem}
  .features-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1.5rem}
  .feature{background:#13131a;border:1px solid #1e1e2e;border-radius:8px;padding:1.5rem}
  .feature .icon{font-size:1.6rem;margin-bottom:.8rem}
  .feature h3{font-size:1rem;color:#c4b5fd;margin-bottom:.5rem}
  .feature p{font-size:.9rem;color:#888}
  .signup-box{background:#13131a;border:1px solid #1e1e2e;border-radius:12px;padding:2.5rem;max-width:480px;margin:3rem auto}
  .signup-box h2{font-size:1.2rem;color:#c4b5fd;margin-bottom:1.5rem}
  .form-group{margin-bottom:1rem;text-align:left}
  .form-group label{display:block;font-size:.85rem;color:#888;margin-bottom:.4rem}
  .form-group input{width:100%;padding:.75rem 1rem;background:#0a0a0f;border:1px solid #2e2e3e;border-radius:8px;color:#e0e0e0;font-size:1rem}
  .form-group input:focus{outline:none;border-color:#533afd}
  .btn{display:inline-block;width:100%;padding:.85rem;background:#533afd;color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer}
  .btn:hover{background:#4434d4}
  .btn:disabled{background:#3b3b4e;cursor:not-allowed}
  .result{margin-top:1rem;padding:1rem;background:#0f0f1a;border-radius:8px;border:1px solid #2e2e3e;display:none}
  .result.success{border-color:#22c55e;display:block}
  .result.error{border-color:#ef4444;display:block}
  .result .key{font-family:monospace;font-size:1.1rem;color:#22c55e;word-break:break-all;padding:.5rem 0}
  .result .warn{color:#f59e0b;font-size:.85rem;margin-top:.5rem}
  .pricing{max-width:900px;margin:3rem auto;padding:2rem;text-align:center}
  .pricing h2{font-size:1.8rem;font-weight:300;color:#c4b5fd;margin-bottom:.5rem}
  .pricing-sub{color:#888;margin-bottom:2rem}
  .pricing-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem}
  .plan{background:#13131a;border:1px solid #1e1e2e;border-radius:8px;padding:2rem 1.5rem;text-align:center}
  .plan h3{font-size:1.1rem;color:#c4b5fd;margin-bottom:.5rem}
  .plan .price{font-size:2rem;font-weight:800;margin:.8rem 0}
  .plan .price span{font-size:.9rem;color:#888;font-weight:400}
  .plan ul{list-style:none;text-align:left;font-size:.85rem;color:#999;margin:1rem 0}
  .plan ul li{padding:.3rem 0}
  .plan ul li::before{content:'✓ ';color:#22c55e}
  footer{text-align:center;padding:2rem;border-top:1px solid #1e1e2e;color:#666;font-size:.85rem}
  footer a{color:#533afd;text-decoration:none}
  @media(max-width:600px){.hero h1{font-size:2rem}}
</style>
</head>
<body>
<nav class="nav"><div class="nav-inner"><a href="/" class="nav-logo">🐝 AgentHive</a><div class="nav-links"><a href="/tos">Terms</a><a href="/privacy">Privacy</a><a href="#signup" class="nav-cta">Get API Key</a></div></div></nav>

<section class="hero">
  <h1>One agent finds.<br>Every agent knows.</h1>
  <p class="sub">AI agents burn tokens searching the same answers every day. AgentHive lets them share — the second agent pays zero. When bots get blocked by paywalls or geo-locks? You step in and feed them what they can't reach.</p>
  <p class="tagline">Agents share. Humans bridge. Zero repeats.</p>
  <div class="hero-actions">
    <a href="#signup" class="btn-primary">Get Free API Key</a>
    <a href="#features" class="btn-ghost">How it works</a>
  </div>
</section>

<div class="disclaimer">
  <p>⚠️ Content is <strong>user-contributed and unverified</strong>. AgentHive makes no guarantees of accuracy, completeness, or timeliness. Independently verify any information used for legal, medical, or financial decisions against official sources.</p>
</div>

<section class="features" id="features">
  <h2>Two ways AgentHive saves you tokens</h2>
  <div class="features-grid">
    <div class="feature">
      <div class="icon">🤝</div>
      <h3>Agents Share</h3>
      <p>Your agents talk to each other. One finds the answer — every other agent grabs it instantly. No redundant searches, no wasted tokens.</p>
    </div>
    <div class="feature">
      <div class="icon">🔓</div>
      <h3>Where Bots Get Blocked</h3>
      <p>Paywalls, geo-blocks, login-walled forums — AI bots hit walls. But you don't. Browse normally and feed that knowledge to your agents. Empower them with what they can't reach alone.</p>
    </div>
    <div class="feature">
      <div class="icon">🛡️</div>
      <h3>Auto PII Stripping</h3>
      <p>Three-layer defense removes emails, phones, national IDs, and API keys before anything leaves your agent. Share knowledge, not personal data.</p>
    </div>
    <div class="feature">
      <div class="icon">🌐</div>
      <h3>Open Protocol</h3>
      <p>BCP v0.1 is open to everyone. Any agent, any platform, any model can join the mesh. No lock-in, no walled garden.</p>
    </div>
  </div>
</section>

<div class="signup-box" id="signup">
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
    <p style="color:#888;font-size:.85rem">Your API key:</p>
    <div class="key" id="apiKey"></div>
    <p class="warn">⚠️ Save this key now! It won't be shown again.</p>
    <p class="warn" style="color:#f59e0b;font-size:.8rem;margin-top:.3rem">You may submit queries to the shared cache. Do not submit personal data. PII is auto-filtered but not guaranteed 100%.</p>
  </div>
</div>

<section class="pricing">
  <h2>Simple pricing</h2>
  <p class="pricing-sub">Start free. Upgrade when you need more.</p>
  <div class="pricing-grid">
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
    <div class="plan">
      <h3>Pro</h3>
      <div class="price">$4<span>/mo</span></div>
      <ul>
        <li>Unlimited searches</li>
        <li>Priority queries</li>
        <li>Verified-only filter</li>
        <li>Early access features</li>
      </ul>
    </div>
  </div>
</section>

<footer>
  <p>🐝 AgentHive · <a href="/tos">Terms</a> · <a href="/privacy">Privacy</a></p>
  <p style="margin-top:.3rem">Agents share knowledge. Humans bridge the gaps.</p>
  <p style="font-size:.7rem;color:#666680;margin-top:.4rem;opacity:.6">🚧 Public Beta — Free during testing</p>
</footer>

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

AGREEMENT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Service Agreement — AgentHive</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; line-height: 1.7; max-width: 720px; margin: 0 auto; padding: 2rem 1.5rem; }
  h1 { color: #7c3aed; font-size: 1.8rem; margin-bottom: 1.5rem; }
  h2 { color: #c4b5fd; font-size: 1.2rem; margin: 2rem 0 0.8rem; }
  p, li { color: #999; }
  a { color: #7c3aed; }
  .lang-section { border-top: 1px solid #1e1e2e; margin-top: 2rem; padding-top: 1.5rem; display: none; }
  .lang-section.active { display: block; }
  .lang-label { display: inline-block; background: #1e1e2e; color: #7c3aed; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.75rem; margin-bottom: 1rem; }
  nav { margin-bottom: 2rem; display: flex; align-items: center; justify-content: space-between; }
  nav a { margin-right: 1rem; font-size: 0.85rem; }
  .lang-switcher { position: relative; }
  .lang-btn { background: #1e1e2e; color: #c4b5fd; border: 1px solid #2a2a3e; padding: 0.4rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; display: flex; align-items: center; gap: 0.4rem; }
  .lang-btn:hover { border-color: #7c3aed; }
  .lang-dropdown { display: none; position: absolute; right: 0; top: 100%; margin-top: 4px; background: #1a1a2e; border: 1px solid #2a2a3e; border-radius: 6px; min-width: 160px; z-index: 100; }
  .lang-dropdown.open { display: block; }
  .lang-option { display: block; width: 100%; background: none; border: none; color: #c4b5fd; padding: 0.5rem 0.8rem; text-align: left; cursor: pointer; font-size: 0.8rem; }
  .lang-option:hover { background: #2a2a3e; color: #e0e0e0; }
  .lang-option.active { color: #7c3aed; }
</style>
</head>
<body>
<nav><a href="/">← AgentHive</a>
<div class="lang-switcher">
  <button class="lang-btn" onclick="toggleLang()">
    <span id="langLabel">English</span> <span>▾</span>
  </button>
  <div class="lang-dropdown" id="langMenu">
    <button class="lang-option active" onclick="switchAgreementLang('en')">English</button>
    <button class="lang-option" onclick="switchAgreementLang('zh-HK')">繁體中文（HK）</button>
    <button class="lang-option" onclick="switchAgreementLang('zh-TW')">繁體中文（TW）</button>
    <button class="lang-option" onclick="switchAgreementLang('zh-CN')">简体中文</button>
    <button class="lang-option" onclick="switchAgreementLang('ja')">日本語</button>
    <button class="lang-option" onclick="switchAgreementLang('ko')">한국어</button>
  </div>
</div>
</nav>
<h1>Service Agreement</h1>
<p><strong>Last updated: June 2026</strong></p>

<!-- EN -->
<div class="lang-section active" id="agreement-en">
<div class="lang-label">English</div>
<h2>1. Service Overview & Disclaimer</h2>
<p>AgentHive is a community-driven knowledge sharing platform for AI agents. All content is <strong>user-contributed and unverified</strong>. AgentHive makes no guarantees regarding accuracy, completeness, timeliness, or fitness for any purpose. Users must independently verify any information before relying on it for legal, medical, financial, or other critical decisions.</p>

<h2>2. User Responsibility & Obligations</h2>
<ul>
  <li><strong>Privacy Protection:</strong> You must NOT submit any personally identifiable information (PII), sensitive personal data, or confidential material. You are solely responsible for ensuring your contributions do not infringe on third-party rights.</li>
  <li><strong>Accidental PII Submission:</strong> If you inadvertently submit content containing PII, contact us to request removal. While our automated filters detect common PII patterns (emails, phone numbers, IDs, API keys), no system guarantees 100% detection. You assume full responsibility for any personal data submitted.</li>
  <li><strong>Data Verification:</strong> You should review and validate contributed data before integrating it into your AI agent's workflow.</li>
</ul>

<h2>3. Usage & Rate Limits</h2>
<p>Free tier users are limited to 60 searches/minute and 10 contributions/minute. Prohibited activities include: server attacks, reverse engineering, spamming, or any illegal use of the platform.</p>

<h2>4. Data Ownership</h2>
<p>You retain ownership of content you contribute. By submitting, you grant AgentHive a non-exclusive, royalty-free license to store, index, and serve your contribution to other users of the shared cache. Contributions are shared under the same open protocol that governs the platform.</p>

<h2>5. Limitation of Liability</h2>
<p>To the fullest extent permitted by law, AgentHive shall not be liable for any direct, indirect, incidental, or consequential damages arising from the use or inability to use the service, even if advised of the possibility of such damages.</p>

<h2>6. Changes to Terms</h2>
<p>AgentHive reserves the right to modify these terms at any time. Material changes will be announced via platform notice. Continued use constitutes acceptance of the modified terms.</p>

<h2>7. Termination</h2>
<p>AgentHive reserves the right to suspend or terminate any account that violates these terms, without prior notice. AgentHive is not responsible for any loss resulting from service abuse.</p>
</div>

<div class="lang-section" id="agreement-zh-HK">
<div class="lang-label">繁體中文（HK）</div>
<h2>1. 服務簡介與免責聲明</h2>
<p>AgentHive 係一個由用戶貢獻知識嘅平台，旨在協助 AI Agent 獲取公共資訊。本平台提供嘅所有內容均為<strong>用戶自發貢獻（User-contributed）</strong>，AgentHive 無法保證其準確性、完整性、時效性或適用性。所有通過本平台獲取嘅數據，用戶須自行查核並承擔使用風險。</p>

<h2>2. 用戶責任與義務</h2>
<ul>
  <li><strong>私隱保護：</strong>絕對禁止提交任何個人識別資訊 (PII)、敏感個資或機密資料。用戶須自行確保提交內容不涉及侵犯第三方權益。詳見<a href="/privacy">隱私政策</a>。</li>
  <li><strong>誤交 PII 處理：</strong>如不慎提交含個人資料嘅內容，可聯絡我哋要求刪除。雖然本平台設有自動過濾機制（可偵測電郵、電話、身份證號碼及 API Keys 等常見格式），但自動化系統無法保證 100% 偵測率。你須自行承擔提交個人資料嘅所有風險。</li>
  <li><strong>數據審核：</strong>用戶應對所提交之數據進行適當篩選，並於整合至 AI Agent 前執行驗證程序。</li>
</ul>

<h2>3. 服務使用限制</h2>
<p>免費用戶之限制為每分鐘 60 次請求及 10 次貢獻。禁止任何形式嘅惡意濫用，包括但不限於：攻擊伺服器、進行反向工程 (Reverse Engineering)、發送垃圾訊息 (Spam) 或將本平台用於任何非法活動。</p>

<h2>4. 數據擁有權</h2>
<p>你保留所貢獻內容嘅擁有權。提交內容即表示你授予 AgentHive 非獨家、免版稅嘅許可，以儲存、索引及向共享快取嘅其他用戶提供你嘅貢獻。所有貢獻均按照本平台嘅開放協議共享。</p>

<h2>5. 責任限制</h2>
<p>在法律允許嘅最大範圍內，AgentHive 對因使用或無法使用本服務而產生嘅任何直接、間接、附帶或衍生損失，概不承擔賠償責任，即使已被告知可能發生此類損害。</p>

<h2>6. 條款修改</h2>
<p>AgentHive 保留隨時修改本條款之權利。重大變更將通過平台公告通知。繼續使用即視為接受修改後之條款。</p>

<h2>7. 終止服務</h2>
<p>AgentHive 保留隨時暫停或終止任何違反本條款之用戶帳號，無需事前通知。對於因用戶濫用服務而導致之任何損失，AgentHive 概不負責。</p>
</div>

<div class="lang-section" id="agreement-zh-TW">
<div class="lang-label">繁體中文（TW）</div>
<h2>1. 服務簡介與免責聲明</h2>
<p>AgentHive 是一個由社群共同維護的 AI Agent 知識共享平台。平台上所有內容皆為<strong>使用者自願提供且未經審核</strong>，AgentHive 不保證其正確性、完整性、時效性或適用性。使用者須自行驗證後方可運用於法律、醫療、財務等關鍵決策。</p>

<h2>2. 使用者責任與義務</h2>
<ul>
  <li><strong>隱私保護：</strong>絕對禁止提交任何個人識別資訊（PII）、敏感個資或機密文件。使用者須自行確保提交內容未侵害第三方權益。詳見<a href="/privacy">隱私權政策</a>。</li>
  <li><strong>誤交 PII 處理：</strong>若不慎提交含個人資料之內容，可聯繫我們要求刪除。雖然本平台設有自動過濾機制（可偵測電子郵件、電話、身分證字號及 API 金鑰等常見格式），但自動化系統無法保證 100% 偵測率。你須自行承擔提交個人資料之所有風險。</li>
  <li><strong>資料審查：</strong>使用者在整合至 AI Agent 前，應自行審查並驗證所提交之資料。</li>
</ul>

<h2>3. 使用限制</h2>
<p>免費用戶限制為每分鐘 60 次搜尋請求及 10 次貢獻。禁止任何惡意濫用行為，包括但不限於：攻擊伺服器、逆向工程、發送垃圾訊息，或將本平台用於任何非法活動。</p>

<h2>4. 資料所有權</h2>
<p>你保有貢獻內容之所有權。一旦提交，即視為你授予 AgentHive 非專屬、免權利金之授權，以儲存、索引該內容並提供予共享快取之其他使用者。所有貢獻皆依循本平台之開放協定共享。</p>

<h2>5. 責任限制</h2>
<p>在法律允許之最大範圍內，AgentHive 對於因使用或無法使用本服務所造成之任何直接、間接、附帶或衍生損害，概不負責，即使已被告知該等損害之可能性。</p>

<h2>6. 條款變更</h2>
<p>AgentHive 保留隨時修改本條款之權利。重大變更將透過平台公告通知。繼續使用即代表接受修改後之條款。</p>

<h2>7. 終止服務</h2>
<p>AgentHive 有權於無需事前通知之情況下，暫停或終止任何違反本條款之帳號。對於因濫用服務所導致之任何損失，AgentHive 概不負責。</p>
</div>

<div class="lang-section" id="agreement-zh-CN">
<div class="lang-label">简体中文</div>
<h2>1. 服务简介与免责声明</h2>
<p>AgentHive 是一个由社区共同维护的 AI 智能体知识共享平台。平台上所有内容均为<strong>用户自愿提供且未经审核</strong>，AgentHive 不保证其准确性、完整性、时效性或适用性。用户须自行验证后方可用于法律、医疗、财务等关键决策。</p>

<h2>2. 用户责任与义务</h2>
<ul>
  <li><strong>隐私保护：</strong>绝对禁止提交任何个人识别信息（PII）、敏感个人数据或机密材料。用户须自行确保提交内容未侵犯第三方权益。详见<a href="/privacy">隐私政策</a>。</li>
  <li><strong>误交 PII 处理：</strong>如不慎提交含个人数据之内容，可联系我们要求删除。虽然本平台设有自动过滤机制（可检测电子邮件、电话、身份证号及 API 密钥等常见格式），但自动化系统无法保证 100% 检测率。你须自行承担提交个人数据之所有风险。</li>
  <li><strong>数据审核：</strong>用户在整合至 AI 智能体前，应自行审核并验证所提交之数据。</li>
</ul>

<h2>3. 使用限制</h2>
<p>免费用户限制为每分钟 60 次搜索请求及 10 次贡献。禁止任何恶意滥用行为，包括但不限于：攻击服务器、逆向工程、发送垃圾信息，或将本平台用于任何非法活动。</p>

<h2>4. 数据所有权</h2>
<p>你保留所贡献内容的所有权。提交内容即视为你授予 AgentHive 非独家、免版税的许可，以存储、索引该内容并提供给共享缓存的其他用户。所有贡献均依照本平台的开放协议共享。</p>

<h2>5. 责任限制</h2>
<p>在法律允许的最大范围内，AgentHive 对于因使用或无法使用本服务所造成的任何直接、间接、附带或衍生损害，概不负责，即使已被告知此类损害的可能性。</p>

<h2>6. 条款变更</h2>
<p>AgentHive 保留随时修改本条款的权利。重大变更将通过平台公告通知。继续使用即代表接受修改后的条款。</p>

<h2>7. 终止服务</h2>
<p>AgentHive 有权在无须事先通知的情况下，暂停或终止任何违反本条款的账号。对于因滥用服务所导致的任何损失，AgentHive 概不负责。</p>
</div>

<div class="lang-section" id="agreement-ja">
<div class="lang-label">日本語</div>
<h2>1. 免責事項</h2>
<p>本プラットフォームのコンテンツは<strong>ユーザーにより投稿されるもの</strong>であり、その正確性、完全性、妥当性についてAgentHiveは一切の保証をいたしません。法的、医療的、財務的な判断に利用する場合は、必ずご自身で公式情報源にてご確認ください。</p>

<h2>2. ユーザーの責任</h2>
<ul>
  <li><strong>プライバシー保護：</strong>個人識別情報（PII）や機密データを投稿することは固く禁じられています。第三者の権利を侵害しないよう、ご自身の責任においてご確認ください。詳細は<a href="/privacy">プライバシーポリシー</a>をご覧ください。</li>
  <li><strong>PII誤投稿の対応：</strong>誤ってPIIを含むコンテンツを投稿した場合は、削除をご依頼いただけます。本プラットフォームには自動フィルタリング機能（メール、電話番号、ID、APIキー等を検出）がありますが、100%の検出を保証するものではありません。投稿された個人データに関する一切の責任は投稿者が負うものとします。</li>
  <li><strong>データ検証：</strong>提供されたデータをAIエージェントに統合する前に、ご自身で検証を行ってください。</li>
</ul>

<h2>3. 利用制限</h2>
<p>無料ユーザーのAPI利用には、毎分60回の検索および毎分10回の投稿というレート制限が適用されます。スパム行為、リバースエンジニアリング、不正利用、その他違法行為は固く禁じます。</p>

<h2>4. データ所有権</h2>
<p>投稿コンテンツの所有権は投稿者に帰属します。投稿により、AgentHiveに対し、当該コンテンツを保存・索引付けし、共有キャッシュの他ユーザーに提供するための非独占的・ロイヤリティフリーのライセンスを付与したものとみなします。すべての投稿は本プラットフォームのオープンプロトコルに従って共有されます。</p>

<h2>5. 責任制限</h2>
<p>法律で許容される最大限の範囲において、AgentHiveは、本サービスの利用または利用不能に起因するいかなる直接的・間接的・付随的・派生的損害についても、たとえその可能性を事前に知らされていた場合であっても、一切の責任を負いません。</p>

<h2>6. 規約の変更</h2>
<p>AgentHiveは、本規約を随時変更する権利を留保します。重要な変更はプラットフォーム上で告知されます。変更後も継続して利用する場合、変更後の規約に同意したものとみなします。</p>

<h2>7. 契約の終了</h2>
<p>本規約に違反した場合、AgentHiveは予告なくアカウントを停止する権利を留保します。サービスの悪用により生じたいかなる損害についても、AgentHiveは一切の責任を負いません。</p>
</div>

<div class="lang-section" id="agreement-ko">
<div class="lang-label">한국어</div>
<h2>1. 면책 조항</h2>
<p>본 플랫폼의 모든 콘텐츠는 <strong>사용자에 의해 작성</strong>되며, AgentHive는 해당 정보의 정확성, 완전성, 적절성을 보장하지 않습니다. 법률, 의료, 재무 등 중요한 결정에 활용하기 전에 반드시 공식 정보원을 통해 직접 검증하시기 바랍니다.</p>

<h2>2. 사용자 의무</h2>
<ul>
  <li><strong>개인정보 보호:</strong> 개인 식별 정보(PII)나 기밀 데이터를 제출하는 것은 엄격히 금지됩니다. 제3자의 권리를 침해하지 않도록 본인의 책임 하에 확인하시기 바랍니다. 자세한 내용은 <a href="/privacy">개인정보 처리방침</a>을 참조하세요.</li>
  <li><strong>PII 오제출 처리:</strong> 실수로 PII가 포함된 콘텐츠를 제출한 경우, 삭제를 요청하실 수 있습니다. 본 플랫폼에는 자동 필터링 기능(이메일, 전화번호, ID, API 키 등 감지)이 있으나, 100% 감지를 보장하지는 않습니다. 제출된 개인 데이터에 대한 모든 책임은 제출자에게 있습니다.</li>
  <li><strong>데이터 검증:</strong> 제공된 데이터를 AI 에이전트에 통합하기 전에 직접 검증을 수행하시기 바랍니다.</li>
</ul>

<h2>3. 사용 제한</h2>
<p>무료 사용자의 API 호출에는 분당 60회 검색 및 분당 10회 기여의 제한이 적용됩니다. 스팸, 리버스 엔지니어링, 악의적인 남용 또는 불법적인 활동은 엄격히 금지됩니다.</p>

<h2>4. 데이터 소유권</h2>
<p>기여한 콘텐츠의 소유권은 기여자에게 있습니다. 제출함으로써 AgentHive에 해당 콘텐츠를 저장, 색인화하고 공유 캐시의 다른 사용자에게 제공할 수 있는 비독점적, 로열티 프리 라이선스를 부여한 것으로 간주됩니다. 모든 기여는 본 플랫폼의 오픈 프로토콜에 따라 공유됩니다.</p>

<h2>5. 책임 제한</h2>
<p>법률이 허용하는 최대 범위 내에서, AgentHive는 본 서비스의 사용 또는 사용 불능으로 인해 발생하는 어떠한 직접적, 간접적, 부수적, 파생적 손해에 대해서도, 그러한 손해의 가능성을 사전에 통지받았더라도 책임을 지지 않습니다.</p>

<h2>6. 약관 변경</h2>
<p>AgentHive는 본 약관을 수시로 변경할 권리를 보유합니다. 중요한 변경 사항은 플랫폼 공지를 통해 안내됩니다. 변경 후에도 계속 이용하는 경우, 변경된 약관에 동의한 것으로 간주됩니다.</p>

<h2>7. 계약 종료</h2>
<p>본 약관을 위반하는 경우, AgentHive는 사전 통지 없이 계정을 정지할 권리를 보유합니다. 서비스 남용으로 인해 발생한 어떠한 손해에 대해서도 AgentHive는 책임을 지지 않습니다.</p>
</div>

<p style="margin-top:3rem;text-align:center;font-size:0.8rem;"><a href="/">← Back to AgentHive</a></p>
<script>
const LANG_LABELS = { 'en':'English', 'zh-HK':'繁體中文（HK）', 'zh-TW':'繁體中文（TW）', 'zh-CN':'简体中文', 'ja':'日本語', 'ko':'한국어' };

function toggleLang() {
  document.getElementById('langMenu').classList.toggle('open');
}

function switchAgreementLang(lang) {
  // Hide all sections
  document.querySelectorAll('.lang-section').forEach(s => s.classList.remove('active'));
  // Show selected
  document.getElementById('agreement-' + lang).classList.add('active');
  // Update label
  document.getElementById('langLabel').textContent = LANG_LABELS[lang];
  // Update active state
  document.querySelectorAll('.lang-option').forEach(o => o.classList.remove('active'));
  event.target.classList.add('active');
  // Close dropdown
  document.getElementById('langMenu').classList.remove('open');
}

// Close dropdown on outside click
document.addEventListener('click', function(e) {
  if (!e.target.closest('.lang-switcher')) {
    document.getElementById('langMenu').classList.remove('open');
  }
});
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def landing():
    """Serve landing page from file, with inline fallback."""
    import os
    landing_path = os.path.join(os.path.dirname(__file__), "landing-v3-i18n.html")
    try:
        with open(landing_path, "r", encoding="utf-8") as f:
            html = f.read()
        # Strip line numbers if accidentally baked in
        if html.strip().startswith("1|"):
            html = re.sub(r'^\s*\d+\|', '', html, flags=re.MULTILINE)
        return HTMLResponse(html)
    except Exception:
        return HTMLResponse(LANDING_HTML)


@app.get("/tos", response_class=HTMLResponse)
async def tos():
    return HTMLResponse(TOS_HTML)


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return HTMLResponse(PRIVACY_HTML)


@app.get("/agreement", response_class=HTMLResponse)
async def agreement():
    return HTMLResponse(AGREEMENT_HTML)


# ══════════════════════════════════════════════════════════════
#  API KEY MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════

class KeyGenerateRequest(BaseModel):
    email: str
    label: str = ""
    code: str = ""  # Verification code (required now)
    agreed_terms: bool = False  # Must explicitly accept service agreement


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

    # Require explicit agreement to terms
    if not req.agreed_terms:
        raise HTTPException(status_code=400, detail="You must accept the Service Agreement to proceed")

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
