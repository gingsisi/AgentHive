# Bot Collective Protocol (BCP) v0.1.0

## An Open Protocol for Agent Knowledge Sharing

---

## 1. Overview

The Bot Collective Protocol (BCP) enables AI agents to contribute and retrieve non-personal knowledge through a shared, privacy-preserving vector cache.

### Design Principles

| Principle | Meaning |
|-----------|---------|
| **Privacy by Default** | Agents auto-classify content before sharing. Private data never leaves the agent. |
| **On-Demand Retrieval** | Cache is queried only when an agent needs it — no broadcast, no push. |
| **Open & Implementable** | Anyone can run a node. The spec is the standard, not any single implementation. |
| **Network Optional** | Start local (single node). Federate when you choose. |
| **Content Expiry** | Knowledge ages. Entries expire by default after 30 days. |

---

## 2. Protocol Endpoints

### Base URL: `{node_url}/bcp/v1`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Node health check |
| `GET` | `/stats` | Node statistics |
| `POST` | `/contribute` | Contribute a knowledge item |
| `GET` | `/search` | Semantic search across the pool |
| `POST` | `/expire` | Admin: purge expired entries |
| `POST` | `/delete/{item_id}` | Remove a contributed item |

---

## 3. Knowledge Item Schema

### 3.1 Contribute Request

```json
{
  "query": "original search query text",
  "content": "The cached content (web result, solution, skill template)",
  "source_url": "https://example.com/page (optional)",
  "tags": ["hong-kong", "tax", "disability"],
  "privacy_class": "public | geo_scoped | community_only",
  "user_level": 1,
  "tool_name": "web_search",
  "item_type": "web_cache | skill | solution"
}
```

### 3.2 Item Metadata (Server-Stored)

```json
{
  "id": "wr_a1b2c3d4e5f6",
  "type": "web_cache",
  "query": "original search query text",
  "source_url": "https://example.com/page",
  "tags": ["hong-kong", "tax"],
  "privacy_class": "public",
  "created": "1716297600",
  "expires": "1718889600",
  "verification": "unverified",
  "reproductions": 0,
  "contributor_hash": "sha256(agent_id + salt)"
}
```

### 3.3 Content Classification (Bot-Side)

Agents MUST classify content before contributing. The classification determines what can be shared:

| Classification | Auto-Share? | Examples |
|---------------|-------------|----------|
| `web_result` | ✅ Yes | Browser search, navigate, snapshot output |
| `private` | ❌ Never | Memory, personal files, conversations |
| `mixed` | ⚠️ Review | Terminal output, delegate task results |
| `unknown` | ❌ Never | Unclassified content |

---

## 4. Search API

### Request

```
GET /bcp/v1/search?q=disability+allowance+Hong+Kong&n=3&min_sim=0.4&collection=all
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | required | Search query (natural language) |
| `n` | int | 3 | Max results (1-10) |
| `min_sim` | float | 0.4 | Minimum similarity threshold (0.0-1.0) |
| `collection` | string | `all` | Filter: `web_cache`, `skills`, `solutions`, `all` |

### Response

```json
{
  "query": "disability allowance Hong Kong",
  "hits": [
    {
      "id": "wr_a1b2c3d4e5f6",
      "collection": "web_cache",
      "content": "The Disability Allowance in Hong Kong requires certification by...",
      "distance": 0.23,
      "type": "web_cache",
      "query": "SWD disability allowance application",
      "source_url": "https://www.swd.gov.hk/en/...",
      "tags": "hong-kong,disability,swd",
      "verification": "verified",
      "reproductions": 4,
      "created": "1716297600",
      "freshness_score": 0.87,
      "trust_score": 0.94,
      "trust_level": "high",
      "stale_warning": null
    }
  ],
  "count": 1,
  "from_cache": true
}
```

### 4.5 Trust Scoring & Freshness

Trust is NOT binary (verified/unverified). It's a continuous score combining accuracy and freshness.

**Trust Score Formula:**

```
Trust = (reproduction_weight × 0.4) + (source_authority × 0.3) + (freshness × 0.3)

Where:
  reproduction_weight = min(reproduction_count / 5, 1.0)
  source_authority    = domain_based (1.0=.gov.hk, 0.8=.edu, 0.6=known NGO, 0.3=blog)
  freshness           = max(1.0 - (age_in_days / domain_decay_days), 0.0)
```

**Domain-Specific Decay Rates:**

| Domain | Refresh Cycle | Decay Period | Examples |
|--------|:---:|:---:|----------|
| `finance` | Minutes-Days | 1 day | Stock prices, exchange rates |
| `policy` | Weekly-Monthly | 90 days | Government circulars, announcements |
| `tax` | Yearly | 365 days | Tax allowances, rates (Budget day reset) |
| `welfare` | 2-3 Years | 730 days | Disability allowance, CSSA rules |
| `education` | Yearly | 365 days | P1 admission, secondary school places |
| `law` | 1-5 Years | 1095 days | Ordinances, regulations |
| `medical` | 1-3 Years | 730 days | Clinical guidelines, drug approvals |
| `tech-creative` | 2-5 Years | 1460 days | 3D printing, design techniques |
| `tech-dev` | Per version | 365 days | Godot, Python APIs (version-tagged) |
| `evergreen` | Never | ∞ | Math, fundamentals, timeless knowledge |

**Trust Levels:**

| Level | Score Range | Bot Behavior |
|-------|:---:|-------------|
| 🟢 **High** | ≥ 0.85 | Use directly. No re-verification needed. |
| 🟡 **Medium** | 0.50-0.84 | Use as starting point. Note: "According to cache..." |
| 🟠 **Low** | 0.25-0.49 | Use with warning. "Unverified cached info, suggest confirming." |
| 🔴 **Stale** | < 0.25 | Show only if nothing else available. "This cached info may be outdated." |

**Stale Warnings:**

When freshness decays below threshold, the system adds a contextual warning:

```
"⚠️ This cached information is 18 months old.
Tax allowances typically change annually (Budget Day).
Consider checking the latest IRD circular."
```

**Accuracy vs Freshness Distinction:**

- **Verified × 3 + Stale** → Information WAS accurate, MAY be outdated → 🟡 Use with caution
- **Unverified + Fresh** → Recently contributed, no validations yet → 🟠 Use with warning
- **Verified × 3 + Fresh** → Gold standard → 🟢 Use directly
- **Disputed** → Someone flagged this → 🔴 Don't use regardless of other scores

---

## 5. User Sharing Levels

| Level | Name | Behavior |
|:-----:|------|----------|
| 0 | 🔒 Ghost | Consume cache only. Never contribute. |
| 1 | 🤝 Auto Web | Auto-share browser/web search results (PII stripped) |
| 2 | 📚 Skills | Level 1 + share skill templates, debugged workflows |
| 3 | 👐 Open | Share everything non-PII. Review queue for mixed items. |

---

## 6. Privacy & Security

### 6.1 PII Stripping

All content contributed at Level 1+ MUST be stripped of:

| Pattern | Replacement |
|---------|-------------|
| Email addresses | `[EMAIL]` |
| Phone numbers | `[PHONE]` |
| HKID / SSN numbers | `[HKID]` / `[SSN]` |
| IP addresses | `[IP]` |
| API keys / tokens | `[CREDENTIAL]` |
| Credit card numbers | `[CARD]` |

### 6.2 Contributor Anonymity

- Contributor identity is hashed with a per-node salt: `sha256(agent_id + node_salt)`
- The salt is rotated periodically
- No agent can be traced back to a specific human from the cache alone

### 6.3 Right to Delete

- Any contributor can remove their items via `DELETE /bcp/v1/delete/{item_id}`
- Items auto-expire after 30 days (configurable per-node)
- Expired items are purged automatically or via admin endpoint

---

## 7. Federation (Future)

Nodes MAY optionally federate to form a larger knowledge mesh:

```
Node A ←→ Node B ←→ Node C
   ↕         ↕         ↕
 Agent     Agent     Agent
```

Federation rules (TBD):
- Nodes announce their public endpoints
- Search queries are forwarded to trusted peers
- Content privacy class is respected across nodes
- `geo_scoped` content stays within geographic region
- `community_only` content stays within the originating node's community

---

## 8. Reference Implementation

A reference implementation is provided at `reference/server.py`:

```bash
git clone https://github.com/xxx/bot-collective
cd bot-collective
pip install -r requirements.txt
python server.py  # Listens on :8050
```

Stack: FastAPI + ChromaDB + Ollama (nomic-embed-text)

### Minimal Node Requirements
- Python 3.10+
- 2GB RAM (4GB recommended for >100K entries)
- 10GB disk (for ChromaDB persistence)
- Ollama (optional; falls back to sentence-transformers)

---

## 9. Agent Integration Guide

### For Hermes Agent

```python
# Before any web search, check cache
cache_hits = requests.get(
    f"{NODE_URL}/bcp/v1/search",
    params={"q": search_query, "n": 3}
)
if cache_hits.json()["from_cache"]:
    use_cached_results(cache_hits.json()["hits"])
else:
    results = do_web_search(search_query)
    # Contribute to pool (if user_level >= 1)
    requests.post(
        f"{NODE_URL}/bcp/v1/contribute",
        json={
            "query": search_query,
            "content": results,
            "source_url": url,
            "user_level": user_config["sharing_level"]
        }
    )
```

### For Claude Code / Cursor / Copilot

Implement the same HTTP endpoints. Reference adapter in `adapters/`.

---

## 10. Versioning

BCP uses semantic versioning: `MAJOR.MINOR.PATCH`

- **MAJOR**: Breaking changes to schema or endpoints
- **MINOR**: New features, backward-compatible
- **PATCH**: Bug fixes, clarifications

Current version: `0.1.0` (pre-release, APIs may change)

---

## 11. Governance (Future)

As the protocol matures:
- Specification maintained in a public repository
- RFC process for changes
- Community-elected maintainers
- Reference test suite for node compliance

---

*Bot Collective Protocol — v0.1.0*
*License: MIT*
