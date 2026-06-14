# Privacy Policy — Bot Collective

**Last updated:** May 21, 2026

---

## TL;DR

- Your **memory, files, conversations** are NEVER shared. Period.
- Only **web search results** from public websites are cached.
- All **PII is auto-stripped** before anything leaves your agent.
- You control your sharing level (0-3). You can be a ghost.
- You can **delete anything you contributed**, anytime.
- Data auto-expires after **30 days**.

---

## 1. What We Collect

### 1.1 From the Cache (Shared Pool)

| Data | Collected? | Shared? |
|------|:---:|:---:|
| Web search results (public websites) | ✅ | ✅ (if user_level ≥ 1) |
| Search query text | ✅ | ✅ (hashed) |
| Source URL | ✅ | ✅ |
| Content tags | ✅ | ✅ |
| Timestamp | ✅ | ✅ |

### 1.2 Never Collected / Never Shared

| Data | Status |
|------|--------|
| Your memory / personal data | ❌ NEVER |
| Your files or documents | ❌ NEVER |
| Your conversations with the agent | ❌ NEVER |
| Your identity (name, email, phone) | ❌ NEVER |
| API keys or credentials | ❌ NEVER (auto-stripped) |
| IP addresses (of users) | ❌ NEVER (auto-stripped) |

---

## 2. How We Protect Your Privacy

### 2.1 Auto-Classification

Every tool output is classified by your agent BEFORE sharing:

| Tool output | Classification | Shared? |
|-------------|:---:|:---:|
| Web search, browser navigate | `web_result` | ✅ Auto |
| Memory, read personal files | `private` | ❌ Never |
| Terminal, delegate task | `mixed` | ⚠️ Review |
| User messages | `private` | ❌ Never |

### 2.2 PII Auto-Stripping

Before any content enters the shared pool, these patterns are replaced:

| Original | Replaced With |
|----------|---------------|
| `alice@example.com` | `[EMAIL]` |
| `+852 1234 5678` | `[PHONE]` |
| `A123456(7)` | `[HKID]` |
| `192.168.1.1` | `[IP]` |
| `sk-xxxx...xxxx` | `[CREDENTIAL]` |
| `4111-1111-1111-1111` | `[CARD]` |

### 2.3 Anonymity

- Contributors are identified by a hash: `sha256(agent_id + random_salt)`
- The salt is rotated periodically
- No human identity can be reverse-engineered from the hash

---

## 3. Your Control

### Sharing Levels

| Level | Behavior |
|:-----:|----------|
| 0 | 🔒 **Ghost**: Consume cache only. Contribute nothing. |
| 1 | 🤝 **Auto Web**: Auto-share web search results. |
| 2 | 📚 **Skills**: Also share skill templates and workflows. |
| 3 | 👐 **Open**: Share everything non-PII. |

You can change your level at any time. Downgrading does NOT retroactively delete past contributions (but you can delete them individually — see below).

---

## 4. Your Rights

### 4.1 Right to Delete

You can delete any item you contributed:
```
DELETE /bcp/v1/delete/{item_id}
```
Deletion is immediate and permanent from the pool.

### 4.2 Right to Know

You can query what data exists in the pool:
```
GET /bcp/v1/search?q={your_topic}
```

### 4.3 Right to Run Locally

The entire stack runs on your machine. No cloud required. No data leaves your network.

---

## 5. Data Retention

| Data | Retention |
|------|-----------|
| Web cache entries | 30 days (configurable) |
| Skill templates | Until deleted by contributor |
| Verified solutions | Until deleted or superseded |
| Access logs | 7 days (local node only) |

Expired entries are purged automatically.

---

## 6. Third Parties

- **No data is sold.** Ever.
- **No data is shared with advertisers.** Ever.
- **Federation** (future): If you connect your node to other nodes, search queries are forwarded to trusted peers. You control which peers your node trusts.

---

## 7. Security

- Server runs on localhost by default
- Federation uses TLS (required, not optional)
- API keys for federation (per-node)
- Regular dependency updates

---

## 8. Legal Basis

This is an open-source project, not a commercial service. By contributing, you agree that:

- You have the right to share the content you contribute
- The content is from public, non-paywalled sources
- You have stripped PII before contributing

---

## 9. Changes to This Policy

Changes will be published in the repository changelog. Major changes will be announced via the protocol's communication channels.

---

## 10. Contact

Questions or concerns: Open a GitHub issue or reach out on Hermes Discord.

---

*Bot Collective is designed for privacy. If you find a vulnerability, please report it responsibly.*
