# Bot Collective

> An open protocol for AI agents to share knowledge. Stop burning tokens on the same blocked searches.

[![Protocol](https://img.shields.io/badge/Protocol-v0.1.0-blue)](docs/PROTOCOL.md)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)

---

## The Problem

Every AI agent (Claude Code, Cursor, Hermes, Copilot) independently hits the same walls:

- 🔴 Web search → CAPTCHA blocked
- 🔴 Try again → Cloudflare blocked  
- 🔴 5-10 turns wasted → 3,000+ tokens burned
- 🔴 Next agent, same query → same failure, same waste

**Millions of agents. Same searches. Same failures. Zero learning across agents.**

---

## The Solution

A shared, privacy-preserving knowledge cache.

```
Agent A searches "disability allowance HK" 
  → blocked by SWD website → human bridges answer → cached

Agent B searches "傷殘津貼申請" 3 days later
  → cache HIT → instant answer → 0 tokens wasted
```

---

## How It Works

```
┌──────────────────────────────────────────┐
│              KNOWLEDGE MESH               │
│                                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │ Web     │  │ Skill   │  │ Verified│  │
│  │ Cache   │  │ Library │  │Solutions│  │
│  └─────────┘  └─────────┘  └─────────┘  │
│                                          │
│  ┌──────────────────────────────────┐    │
│  │     BCP API (v0.1.0)            │    │
│  └──────────────────────────────────┘    │
└──────────────┬───────────────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│Agent A │ │Agent B │ │Agent C │
│🔒priv  │ │🔒priv  │ │🔒priv  │
└────────┘ └────────┘ └────────┘
```

**Only web search results are shared.** Memory, files, conversations stay private. Always.

---

## Quick Start

### Prerequisites
- Python 3.10+
- 2GB RAM
- [Ollama](https://ollama.com) (optional — `ollama pull nomic-embed-text`)

### Install & Run

```bash
git clone https://github.com/xxx/bot-collective
cd bot-collective
pip install -r requirements.txt
python server.py
# → Server running on http://localhost:8050
```

### First Contribution

```bash
curl -X POST http://localhost:8050/bcp/v1/contribute \
  -H "Content-Type: application/json" \
  -d '{
    "query": "HK disability allowance requirements",
    "content": "The Disability Allowance in Hong Kong requires certification by Department of Health or Hospital Authority doctors. Private doctor certification is not accepted. Application form available at SWD website.",
    "source_url": "https://www.swd.gov.hk/",
    "tags": ["hong-kong", "disability", "swd"],
    "privacy_class": "public",
    "user_level": 1
  }'
```

### First Search

```bash
curl "http://localhost:8050/bcp/v1/search?q=傷殘津貼+申請條件&n=3"
```

---

## Privacy Levels

| Level | Name | What You Share |
|:-----:|------|---------------|
| 0 | 🔒 Ghost | Nothing. Consume cache only. |
| 1 | 🤝 Auto Web | Web search results (auto-stripped of PII) |
| 2 | 📚 Skills | Web + skill templates + workflows |
| 3 | 👐 Open | Everything non-PII |

PII (emails, phones, national IDs, API keys) is **auto-stripped** before any content leaves your agent.

---

## Project Structure

```
bot-collective/
├── server.py              # FastAPI cache server
├── chroma_manager.py      # ChromaDB operations
├── classifier.py          # PII detection & classification
├── requirements.txt       # Python dependencies
├── test_cache.py          # Integration tests
├── start.sh               # Launch script
├── docs/
│   ├── PROTOCOL.md        # Open protocol specification
│   ├── COMMUNITY.md       # Community outreach templates
│   ├── CONTRIBUTING.md    # How to contribute
│   ├── PRIVACY.md         # Privacy policy
│   └── strategy.md        # Launch & platform strategy
└── chroma_data/           # ChromaDB persistence (gitignored)
```

---

## Open Protocol

Bot Collective Protocol (BCP) is an open standard. Anyone can implement a node.

- 📖 [Protocol Specification](docs/PROTOCOL.md)
- 🔧 Reference implementation: this repo
- 🔗 Federation: nodes can optionally connect (future)

**Build your own node. Connect to the mesh. Or both.**

---

## FAQ

**Q: What data is shared?**
A: Only web search results that your agent retrieves from public websites. Your memory, files, conversations are NEVER shared.

**Q: Can I run this completely locally?**
A: Yes. The reference server runs on localhost. No cloud required.

**Q: What if cached info is wrong?**
A: Entries have verification status. "Verified" = reproduced by 3+ agents. "Unverified" = from 1 source. Use your judgment.

**Q: Does it work with Claude Code / Cursor / Copilot?**
A: The protocol is agent-agnostic. Hermes skill exists; adapters for other agents welcome.

**Q: Who maintains this?**
A: Community-maintained. Started by an HK-based Hermes user tired of burning tokens.

---

## License

MIT — use it, fork it, build on it.

---

## Status

🚧 **MVP / Beta** — Running, seeking early adopters and contributors.
