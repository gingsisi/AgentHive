# 🐝 AgentHive

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
  <a href="#-how-it-works">How</a> ·
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-protocol">Protocol</a> ·
  <a href="#-api">API</a> ·
  <a href="#-deploy">Deploy</a>
</p>

---

## 🤔 Why

AI agents can't reach everywhere. Paywalled sites, geo-blocked content, login-walled forums — bots hit walls every day. But humans don't.

AgentHive is a **human-powered knowledge mesh** for AI agents. Real people browse the web — their captures flow into a shared cache. Agents query the mesh instead of burning tokens on blocked searches. **One human finds. Every agent remembers. Zero tokens.**

| Without AgentHive | With AgentHive |
|---|---|
| 🔥 Burn tokens on every search | ⚡ Cache check → if hit, $0 |
| 🚫 Bots blocked everywhere | 👤 Humans go where bots can't |
| 🔁 Same queries, same cost | 🤝 One human scrapes, all agents benefit |
| 🔒 Siloed per-user | 🌐 Open protocol, anyone joins |

---

## 🏗️ How It Works

**Human → Mesh → Agent**

```
👤 Human browses blocked site (Tabelog, Baby Kingdom, gov portals)
📸 Human Bridge captures content → PII stripped → added to mesh
🤖 Agent searches same query → mesh returns cached result → 0 tokens
```

**Human Bridge:** A browser extension that captures web pages as humans browse — turning everyday browsing into contributions. Content passes through 3 layers of PII defense before entering the mesh.
1. **Agent-side PII stripping** — emails, phones, national IDs removed before sharing
2. **User review queue** — pending captures reviewed before contribution
3. **Server-side re-scan** — aggressive regex patterns catch what slipped through

**Trust scoring:**
Entries verified by multiple agents earn higher trust scores. See freshness, source authority, and reproduction counts for every result.

---

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USER/agenthive.git
cd agenthive

# Install
pip install -r requirements.txt

# Run
python3 server.py
# → http://localhost:8081
```

```bash
# Get an API key (2-step email verification)
# Open http://localhost:8081 in your browser

# Search the mesh
curl "http://localhost:8081/search?q=disability+allowance&n=3" \
  -H "X-API-Key: bc_YOUR_KEY"

# Contribute to the mesh
curl -X POST http://localhost:8081/contribute \
  -H "X-API-Key: bc_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"...","content":"...","source_url":"https://...","tags":["hong-kong"]}'
```

---

## 📡 Protocol

AgentHive runs on **BCP v0.1** — an open protocol for shared agent knowledge caching.

- [PROTOCOL.md](docs/PROTOCOL.md) — Full protocol specification
- [community-outreach.md](docs/community-outreach.md) — How to join the mesh
- [CONTRIBUTING.md](CONTRIBUTING.md) — How to contribute

Any agent can join. Any developer can build a node. The protocol is the moat.

---

## 🔌 API

### Public Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Landing page (multi-language: EN/中文/日本語/한국어) |
| `GET /health` | Health check |
| `GET /stats` | Cache statistics |
| `POST /api/keys/request-verification` | Step 1: request email verification code |
| `POST /api/keys/generate` | Step 2: verify code → get API key |
| `GET /tos` | Terms of Service |
| `GET /privacy` | Privacy Policy |

### Authenticated (X-API-Key header)

| Endpoint | Description |
|---|---|
| `GET /search?q=...&n=3` | Semantic search the mesh |
| `POST /contribute` | Contribute a web result |
| `POST /bcp/v1/bridge-capture` | Human Bridge capture (Layer 3 PII scan) |
| `GET /api/keys/stats` | Get your key's usage stats |

### Rate Limits

| Tier | Search | Contribute | Price |
|------|--------|------------|-------|
| Free | 60/min | 10/min | $0/mo |
| Pro | Unlimited | 50/min | $4/mo (coming soon) |

---

## 🌍 Multi-Language

The landing page supports 5 languages with instant switching, no page reload:

- 🇭🇰 繁體中文
- 🇬🇧 English
- 🇯🇵 日本語
- 🇰🇷 한국어
- 🇨🇳 简体中文

---

## ☁️ Deploy

```bash
# Railway / Render (free tier)
# Just point to server.py — it's a single FastAPI app with no external deps

# Environment variables:
#   BC_SYSTEM_KEY  — override the default system key for production

# Docker
docker build -t agenthive .
docker run -p 8081:8081 agenthive
```

---

## 🛡️ Privacy & Security

- All contributed content is **user-contributed and unverified**
- PII is auto-stripped at 3 layers: agent → review → server re-scan
- API keys are SHA-256 hashed in SQLite
- No personal data stored — queries are cached by content hash, not user identity
- Full privacy policy at [PRIVACY.md](PRIVACY.md)

---

## 📦 Tech Stack

- **FastAPI** — REST API
- **ChromaDB** — Vector storage
- **SQLite** — API key management
- **Sentence Transformers** — Embeddings (English)
- **Ollama** — Chinese embeddings (nomic-embed-text)

---

## 🤝 Contributing

AgentHive is open source under MIT License. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

<p align="center">
  <b>🐝 Built for agents. Contributed by humans.</b><br>
  <i>Let agents say High Five. 群蜂聚智。一拍即合。</i>
</p>
