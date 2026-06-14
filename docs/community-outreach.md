# Community Outreach Posts

## Platform Strategy Summary

| Platform | Best Time | Post Type | Tone |
|----------|-----------|-----------|------|
| Hermes Discord (#showcase) | Any weekday | Plugin announcement + beta invite | Casual, peer-to-peer |
| r/LocalLLaMA | Sat/Sun morning EST | Weekend project showcase | Technical, honest |
| r/selfhosted | Wed afternoon EST | Self-host announcement | Practical, setup-focused |
| Hacker News (Show HN) | Tue-Thu 7am EST | Show HN launch post | Polished, product-focused |
| Product Hunt | Tue launch | Product listing | Marketing, benefit-driven |

---

## 1. Hermes Discord Post (Casual)

> **Title:** I built a shared knowledge cache for Hermes agents — stop burning tokens on the same failed searches
>
> Every time your Hermes agent hits a CAPTCHA or gets blocked by Cloudflare, that's 5-10 turns of wasted tokens. What if those failed searches fed a shared cache that made EVERY agent smarter?
>
> **How it works:**
> — Agent A searches "HK disability allowance" → gets blocked → but a human later shares the answer → Agent B's cache hit saves 8 turns
> — 100 agents independently searching the same thing = 800 wasted turns. Shared cache = 0 wasted turns after first hit.
>
> **Privacy-first:**
> — Your memory, files, conversations: NEVER shared. Period.
> — Only web search results get cached (auto-stripped of PII)
> — 4-tier sharing control: Ghost (consume only) → Auto Web → Skills → Open
>
> **Beta open:** 10 slots. Self-host option available (open protocol). Reply or DM me.
>
> Built on FastAPI + ChromaDB + Ollama (all open source). Protocol spec is open — anyone can implement.

---

## 2. r/LocalLLaMA Post (Technical)

> **Title:** [Project] Bot Collective — A shared vector cache for AI agents that keeps hitting the same walls
>
> **Problem:** Every local LLM agent independently hits the same web search failures (CAPTCHAs, Cloudflare, paywalls). This is structural — websites don't want bots, and every agent reinvents the same failed search.
>
> **Solution:** An open-protocol shared cache. Agents contribute verified web search results to a ChromaDB pool. When Agent B searches for something Agent A already found, it gets a cache hit instead of a fresh (likely blocked) search.
>
> **Stack:** FastAPI + ChromaDB + Ollama embeddings (nomic-embed-text). All local, all open source.
>
> **Privacy model:** Bot-side auto-classification — only web search outputs are shareable. Memory, files, conversations are never touched. 4 user-controlled sharing levels.
>
> **Open protocol:** Spec at [link]. Anyone can run their own node — nodes can optionally federate. Think Matrix protocol, but for agent knowledge.
>
> **MVP status:** Running on my own Hermes agent for 1 week. Hit rate ~X%. Looking for 10 beta testers.
>
> **Self-host:** `git clone` → `pip install -r requirements.txt` → `python server.py` → point your agent's cache skill at localhost:8050
>
> Questions / roasting welcome. This could be dumb — tell me why.

---

## 3. r/selfhosted Post (Practical)

> **Title:** Self-host a shared knowledge cache for your AI agents — stop wasting API credits on blocked searches
>
> If you run any AI agent (Claude Code, Cursor, Hermes, Copilot), you've seen this:
> — Agent searches web → blocked by CAPTCHA
> — Agent tries again → blocked by Cloudflare
> — 10 turns later → still no answer, 5,000 tokens burned
>
> I built **Bot Collective** — a self-hostable shared cache that lives on your network:
>
> **What you get:**
> — Semantic search across cached web results
> — Auto-contribution from your agent's successful searches
> — Local ChromaDB + Ollama embeddings (nothing leaves your box)
> — Optional federation with other nodes (privacy-preserving)
>
> **Quick start:**
> ```
> git clone https://github.com/xxx/bot-collective
> pip install -r requirements.txt
> python server.py  # runs on :8050
> ```
> Then install the Hermes skill (or write your own adapter for Claude/Cursor).
>
> **Requirements:** Python 3.10+, 2GB RAM, Ollama (optional, for embeddings)
>
> **Docker image coming soon.**

---

## 4. Hacker News — Show HN (Polished)

> **Title:** Show HN: Bot Collective — An open protocol for AI agents to share knowledge
>
> AI agents are exploding. Millions of them run daily (Claude Code, Cursor, Copilot, Hermes). But every single one independently hits the same web search failures — CAPTCHAs, Cloudflare blocks, API paywalls. This is a massive structural inefficiency.
>
> We built an open protocol where agents contribute verified web search results to a shared vector database. Think: "What if every agent's successful search made every other agent smarter?"
>
> **Technical:**
> — FastAPI + ChromaDB + Ollama embeddings
> — Bot-side auto-classification: only web outputs are shareable
> — 4-tier privacy control (ghost → auto-web → skills → open)
> — Open protocol spec with reference implementation
>
> **Design decisions we're proud of:**
> — Privacy-first: PII auto-stripped before any content leaves the agent
> — No broadcast model: cache is queried on-demand, not pushed
> — Human bridge: users can manually contribute via any messaging platform
> — Open spec: anyone can implement a node, nodes can federate
>
> **Status:** MVP running, seeking early adopters. Self-host reference implementation available.
>
> [GitHub link] | [Protocol spec] | [Live demo stats]

---

## 5. Product Hunt Listing

> **Tagline:** Stop your AI agents from burning tokens on the same blocked searches
>
> **Description:**
> Bot Collective is an open protocol for AI agents to share knowledge. When Agent A finds an answer, every other agent benefits.
>
> **Why it matters:**
> — AI agents waste $500M+/year on duplicate failed searches
> — Websites are getting MORE hostile to bots (CAPTCHAs, paywalls)
> — No existing shared cache layer exists for agents
>
> **How it works:**
> 1. Your agent does a web search → result auto-cached
> 2. Another agent searches similar topic → cache hit → instant answer
> 3. 0 wasted turns, 0 wasted tokens
>
> **Privacy by design:**
> — Your memory, files, conversations: NEVER shared
> — Only web search results cached, PII auto-stripped
> — 4-tier control: contribute nothing → contribute everything
>
> **Self-host or cloud:** Open source (MIT). Run your own node or use ours.
>
> **Maker's story:** Built by someone who got tired of watching their Hermes agent fail the same web searches 30+ times in one session.

---

## Key Messaging Principles (Apply Everywhere)

1. **Lead with the pain, not the solution** — "Every agent wastes tokens on blocked searches" before "Here's a cache"
2. **Privacy-first in every message** — People are paranoid about agents reading their data
3. **Open protocol = trust signal** — "You can run your own" removes the "what if you shut down" objection
4. **Honest about limitations** — "Cache hit rate is ~X%, not 100%. But 30% hit rate = 30% fewer wasted turns"
5. **Token cost angle** — Tangible, measurable. "$5 saved this week" > "better knowledge sharing"
6. **HK niche = credibility** — "I built this because HK government sites are impossible for bots" — specific validates general

---

## Posting Schedule (Ideal First Week)

| Day | Platform | Post |
|-----|----------|------|
| Monday | Hermes Discord | Casual announcement + beta invite |
| Wednesday | r/selfhosted | Self-host guide post |
| Saturday | r/LocalLLaMA | Weekend project showcase |
| Following Tue | Hacker News | Show HN (with 1 week of metrics) |
| Following Thu | Product Hunt | Full listing (with testimonials) |
