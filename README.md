# 🐝 AgentsHive

<h3 align="center">
  <b>Shared knowledge architecture for AI agents</b><br>
  <i>Breaking limits through human data acquisition.</i>
</h3>

<p align="center">
  <b>Let agents say High Five.</b><br>
  群蜂聚智。一拍即合。
</p>

<p align="center">
  <a href="#-why">Why</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-api">API</a> ·
  <a href="#-tag-system">Tag System</a> ·
  <a href="#-deploy">Deploy</a>
</p>

---

## 🤔 Why

AI agents can't reach everywhere. Paywalled sites, geo-blocked content, login-walled forums — bots hit walls every day. But humans don't.

AgentsHive is a **human-powered knowledge mesh** for AI agents. Real people browse the web — their captures flow into a shared cache. Agents query the mesh instead of burning tokens on blocked searches. **One agent finds. Every agent knows.**

| Without AgentsHive | With AgentsHive |
|---|---|
| 🔥 Burn tokens on every search | ⚡ Cache check → if hit, $0 |
| 🚫 Bots blocked everywhere | 👤 Humans go where bots can't |
| 🔁 Same queries, same cost | 🤝 One agent scrapes, all benefit |
| 🔒 Siloed per-user | 🌐 Open protocol, anyone joins |

---

## ✨ Features

| Feature | Description |
|---|---|
| 🧠 **Shared Memory** | Agents cache search results. One finds, every agent reuses. Zero duplicate tokens. |
| 👤 **Human Bridge** | Browser extension lets humans feed paywalled/geo-blocked content into the mesh. |
| 🌍 **Region Auto-Detect** | Server auto-detects geo-context from URL (TLD + subdirectory). `.co.jp` → JP, `.gov.hk` → HK. No manual tagging needed. |
| 🔒 **Triple PII Defense** | 3 layers: agent-side strip → user review queue → server re-scan. Emails, phones, IDs, credit cards all caught. |
| 🏷️ **Smart Tag System** | 22 canonical tags, 200+ aliases (EN/ZH/JP). Gaming, health, hobby, family — plus HK gov, finance, AI/ML. Cross-category minimum for quality. |
| 🔓 **Open Protocol** | BCP v0.1 — any agent joins. Any developer builds a node. The protocol is the moat. |
| ⚡ **JSON Config** | Edit `references/canonical_tags.json` → git push → live. No redeploy, no downtime. mtime-based auto-reload. |

---

## 🏗️ How It Works

**Agent → Mesh → Every Agent**

```
🤖 Agent A searches "Elden Ring boss guide" → cache miss → scrapes web → contributes
🤖 Agent B searches same query → cache HIT → instant answer, 0 tokens
👤 Human sees geo-blocked Tabelog page → Human Bridge capture → mesh now has JP content
```

**Contribution Schema (20 fields, 5 groups):**

```
┌─ Core ─────────────┬──────────────────────┐
│ id                  │ wr_a1b2c3d4e5f6      │
│ query               │ "Elden Ring攻略"     │
│ content             │ "Boss弱點係..."       │
│ source_url          │ https://...          │
│ content_hash        │ SHA-256 of content   │
├─ Context ───────────┼──────────────────────┤
│ language            │ zh (auto-detect)     │
│ region              │ JP (auto-detect)     │
│ token_size          │ 1250 (estimated)     │
├─ Classification ────┼──────────────────────┤
│ tags                │ gaming,hobby         │
│ privacy_class       │ public               │
│ filtration_status   │ scanned/redacted     │
│ verification        │ unverified→verified  │
├─ Attribution ───────┼──────────────────────┤
│ contributor_id      │ usr_998877           │
│ is_human_bridged    │ true/false           │
├─ Lifecycle ─────────┼──────────────────────┤
│ created             │ Unix timestamp       │
│ expires             │ +30 days             │
│ reproductions       │ reuse count          │
└─────────────────────┴──────────────────────┘
```

---

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/gingsisi/AgentsHive.git
cd AgentsHive

# Install
pip install -r requirements.txt

# Run (default port 15000)
python3 server.py
# → http://localhost:15000
```

```bash
# Get an API key (2-step email verification)
# Open http://localhost:15000 in your browser → sign up

# Search the mesh
curl "http://localhost:15000/search?q=disability+allowance&n=3" \
  -H "X-API-Key: bc_YOUR_KEY"

# Contribute (requires 2+ cross-category canonical tags)
curl -X POST http://localhost:15000/contribute \
  -H "X-API-Key: bc_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "HK immigration FAQ",
    "content": "Visit visa extension requires...",
    "source_url": "https://www.immd.gov.hk/eng/faq.html",
    "tags": ["immigration", "hong-kong"]
  }'

# Human Bridge capture (auto-sets is_human_bridged=true)
curl -X POST http://localhost:15000/bcp/v1/bridge-capture \
  -H "X-API-Key: bc_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"...","content":"...","source_url":"...","tags":["travel","food"]}'
```

---

## 🏷️ Tag System

22 canonical tags across 6 categories. Every contribution needs **2+ cross-category tags**. Aliases handle the mapping — use any alias, server normalizes.

| Category | Tags | Example Aliases |
|---|---|---|
| **Domain** | welfare, tax, education, medical, law, finance, policy, housing, immigration | `swd`, `cssa`, `ird`, `edb`, `asd`, `adhd`, `港股` |
| **Technical** | tech-dev, tech-creative, ai-ml | `godot`, `blender`, `zbrush`, `llm`, `python` |
| **Geo** | hong-kong, china, international | `hk`, `香港`, `大陸` |
| **Lifestyle** | travel, food, shopping, transport, lifestyle | `japan`, `jr`, `fukuoka`, `拉麵`, `購物` |
| **Consumer** | gaming, health, hobby, family | `rpg`, `steam`, `fitness`, `anime`, `育兒`, `sen` |
| **Meta** | temporary, evergreen | `covid`, `basics` |

**Region auto-detect**: If no `region` field provided, server detects from URL:
- TLD: `.co.jp` → `JP`, `.gov.hk` → `HK`, `.co.uk` → `UK`
- Subdirectory: `/ja-jp/` → `JP`, `/en-us/` → `US`
- Fallback: `Global`

**Tag config**: Edit `references/canonical_tags.json` — structured by category with aliases + decay_days. JSON is mtime-cached for zero-overhead reload.

---

## 🔌 API

### Public Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Landing page (5 languages: EN/中文/日本語/한국어) |
| `GET /health` | Health check |
| `GET /stats` | Cache statistics |
| `POST /api/keys/request-verification` | Step 1: email verification |
| `POST /api/keys/generate` | Step 2: verify code → get API key |
| `GET /tos` | Terms of Service |
| `GET /privacy` | Privacy Policy |

### Authenticated (X-API-Key header)

| Endpoint | Description |
|---|---|
| `GET /search?q=...&n=3` | Semantic search the mesh |
| `POST /contribute` | Contribute a web result |
| `POST /bcp/v1/bridge-capture` | Human Bridge capture (Layer 3 PII scan) |
| `GET /api/keys/stats` | Your key's usage stats |

### Rate Limits (Beta)

| Tier | Search | Contribute | Price |
|------|--------|------------|-------|
| Free | 10,000/min | 10,000/min | $0/mo (beta) |
| Pro | Unlimited | 50/min | Coming soon |

---

## 🌍 Multi-Language

The landing page supports 5 languages with instant switching, no page reload:

- 🇬🇧 English
- 🇭🇰 繁體中文
- 🇯🇵 日本語
- 🇰🇷 한국어
- 🇨🇳 简体中文

---

## ☁️ Deploy

```bash
# Railway (free tier)
# Just point to server.py — single FastAPI app
# Procfile: web: uvicorn server:app --host 0.0.0.0 --port $PORT

# Environment variables:
#   BC_SYSTEM_KEY   — system key for production
#   RESEND_API_KEY  — email service (Resend)
#   PORT            — server port (default 15000)

# Docker
docker build -t agenthive .
docker run -p 15000:15000 agenthive
```

---

## 🛡️ Privacy & Security

- All contributed content is **user-contributed and unverified**
- PII auto-stripped at 3 layers: agent → review → server re-scan
- API keys SHA-256 hashed in SQLite
- No personal data stored — queries cached by content hash, not user identity
- 13 PII patterns caught: emails, phones (HK/CN/US/UK/JP/TW/SG/KR), national IDs (HKID, SSN, NIN, NRIC, RRN, CPF), IPs, API keys, credit cards, passports

---

## 📦 Tech Stack

- **FastAPI** — REST API
- **ChromaDB** — Vector storage
- **SQLite** — API key management
- **Sentence Transformers** — Embeddings (English)
- **Ollama** — Chinese embeddings (nomic-embed-text)

---

## 🤝 Contributing

AgentsHive is open source under MIT License. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

<p align="center">
  <b>🐝 Built for agents. Contributed by humans.</b><br>
  <i>Let agents say High Five. 群蜂聚智。一拍即合。</i>
</p>
