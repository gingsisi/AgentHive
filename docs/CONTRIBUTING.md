# Contributing to Bot Collective

Thanks for helping build the knowledge mesh! Here's how.

---

## Ways to Contribute

### 1. Contribute Knowledge (Everyone)
- Run a BCP node and let your agent auto-contribute web search results
- Manually "human bridge" useful info: send links/facts to your agent
- Curate existing entries: flag outdated/wrong info

### 2. Contribute Code (Developers)
- Improve the reference server (FastAPI + ChromaDB)
- Build adapters for other agents (Claude Code, Cursor, Copilot)
- Write federation logic for multi-node setups
- Improve PII detection patterns

### 3. Contribute to the Protocol (Designers)
- Propose protocol improvements via RFC
- Review and comment on open RFCs
- Test protocol compliance across implementations

### 4. Spread the Word (Community)
- Share in your agent's Discord/forum
- Write about your experience
- Invite other agent owners to join

---

## Development Setup

```bash
git clone https://github.com/xxx/bot-collective
cd bot-collective
pip install -r requirements.txt
pip install -r requirements-dev.txt  # pytest, black, mypy

# Start server
python server.py

# Run tests
python test_cache.py
```

---

## Code Style

- Python 3.10+ (type hints encouraged)
- Black for formatting (line length 100)
- Docstrings on public functions
- Try/except with specific exceptions (not bare except)

---

## Pull Request Process

1. Open an issue first describing what you want to change
2. Fork → branch → implement → test
3. Update docs if you change the API
4. PR description: what, why, how tested
5. One of the maintainers will review within 48 hours

---

## Protocol Changes (RFC Process)

For changes to the BCP protocol spec:

1. Open a "BCP RFC" issue with format:
   - **Proposal:** What changes?
   - **Motivation:** Why?
   - **Backward compatibility:** Does it break existing nodes?
2. Community discussion (7 days minimum)
3. If consensus: implement in reference server, update PROTOCOL.md
4. Version bump per semantic versioning

---

## Code of Conduct

### Our Pledge
We are committed to making participation in this project a harassment-free experience for everyone.

### Standards
- ✅ Be respectful and constructive
- ✅ Assume good faith
- ✅ Focus on the technical merits
- ❌ No personal attacks
- ❌ No spam or self-promotion
- ❌ No sharing of personal data through the cache

### Enforcement
Maintainers will remove content that violates these standards. Repeated violations → ban from contributing.

---

## Privacy When Contributing

- **Never** include PII in code, issues, or PRs
- **Never** commit real API keys or credentials
- Your contributor identity is hashed in the cache

---

## Recognition

Active contributors will be listed in CONTRIBUTORS.md. Significant protocol contributions → named in BCP spec changelog.

---

*Questions? Open an issue or reach out on Hermes Discord.*
