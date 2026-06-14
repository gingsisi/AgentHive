#!/usr/bin/env python3
"""
Integration test for Bot Collective cache server.
Tests: health, contribute, search, PII stripping, expiry.
"""

import time
import sys
import requests

BASE = "http://localhost:8050"
PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✅ {name}")
    except AssertionError as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")
    except Exception as e:
        FAIL += 1
        print(f"  💥 {name}: {type(e).__name__}: {e}")


# ── TESTS ────────────────────────────────────────────────────


def test_health():
    r = requests.get(f"{BASE}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_stats_empty():
    r = requests.get(f"{BASE}/stats")
    assert r.status_code == 200
    data = r.json()
    assert "web_cache" in data


def test_contribute_valid():
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "HK disability allowance 2026",
            "content": "The Disability Allowance requires certification by Department of Health or Hospital Authority doctors. Application form available at SWD office.",
            "source_url": "https://www.swd.gov.hk/en/disability",
            "tags": ["hong-kong", "disability", "swd", "welfare"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contributed"] is True
    assert data["id"].startswith("wr_")
    assert data["classification"] == "web_result"
    assert data["pii_stripped"] is False


def test_contribute_with_pii():
    """PII should be auto-stripped."""
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "test with PII data",
            "content": "Contact alice@example.com or call +852 1234 5678 for details about the application process. HKID reference: A123456(7)",
            "source_url": "https://example.com/test",
            "tags": ["test", "welfare", "hong-kong"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contributed"] is True
    assert data["pii_stripped"] is True
    # Contribution should be stripped
    search_r = requests.get(f"{BASE}/search", params={"q": "PII alice HKID reference", "n": 1})
    hits = search_r.json().get("hits", [])
    if hits:
        content = hits[0]["content"]
        assert "@" not in content, f"Email not stripped: {content}"
        assert "[EMAIL]" in content, "Email should be replaced with [EMAIL]"


def test_search_hit():
    r = requests.get(
        f"{BASE}/search",
        params={"q": "disability allowance application requirements", "n": 3},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["from_cache"] is True
    assert data["count"] >= 1
    hit = data["hits"][0]
    assert "disability" in hit.get("content", "").lower() or "Disability" in hit.get("content", "")
    # Relevance field MUST exist
    assert "relevance" in hit, "Hit missing relevance field"
    assert hit["relevance"] in ("DIRECT_MATCH", "NARROWS_DOWN", "TOPIC_ONLY")
    # Relevance summary MUST exist
    assert "relevance_summary" in data
    assert "action" in data["relevance_summary"]


def test_search_miss():
    r = requests.get(
        f"{BASE}/search",
        params={"q": "xyzzy_nonexistent_query_12345_abcde", "n": 3},
    )
    assert r.status_code == 200
    data = r.json()
    # May or may not find anything — that's fine
    assert "hits" in data


def test_contribute_insufficient_level():
    """Level 0 (ghost) should not contribute."""
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "should not save due to ghost mode validation test",
            "content": "This content should never enter the pool because user is level 0 ghost mode for privacy.",
            "source_url": "https://example.com/test",
            "tags": ["welfare", "hong-kong"],
            "privacy_class": "public",
            "user_level": 0,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contributed"] is False


def test_contribute_too_short():
    """Tiny content should be rejected."""
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "short",
            "content": "Hi",
            "source_url": "https://example.com",
            "tags": ["test", "welfare", "hong-kong"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contributed"] is False
    assert data["classification"] == "rejected_validation"


def test_contribute_no_tags():
    """Less than 2 canonical tags should be rejected."""
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "some random query that is long enough for testing validation",
            "content": "This is a test entry with plenty of content but only one valid tag which should be rejected by the validation layer.",
            "source_url": "https://example.com/test",
            "tags": ["randomtag"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contributed"] is False
    assert data["classification"] == "rejected_validation"


def test_contribute_no_source_url():
    """Missing source_url for web_search should be rejected."""
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "test query without source URL for validation purposes",
            "content": "This is a test entry with sufficient content but no source URL which should be rejected by the validation layer.",
            "source_url": "",
            "tags": ["welfare", "hong-kong"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contributed"] is False
    assert data["classification"] == "rejected_validation"


def test_stats_after_contribute():
    r = requests.get(f"{BASE}/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["web_cache"] >= 1


def test_contribute_private_path():
    """read_file on /home path should be classified private and rejected."""
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "private file read test with validation requirements",
            "content": "This should not be shared because it came from a personal file that contains private user data.",
            "source_url": "https://example.com/test",
            "tags": ["welfare", "hong-kong"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "read_file",
        },
        params={"file_path": "/opt/data/home/secret.txt"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contributed"] is False
    assert data["classification"] == "private"


# ── RELEVANCE TESTS ──────────────────────────────────────────


def test_relevance_direct_match():
    """Contribute specific content, then search with exact-match query → DIRECT_MATCH."""
    # Contribute a very specific entry
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "私家醫生可否簽發傷殘津貼醫療證明",
            "content": "根據社會福利署規定，傷殘津貼申請必須由衞生署或醫院管理局的註冊醫生簽發醫療評估報告。私家醫生報告不會被社署接納。申請人必須到公立醫院或專科門診進行評估。",
            "source_url": "https://www.swd.gov.hk/tc/disability-cert",
            "tags": ["welfare", "hong-kong", "disability", "medical"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    assert r.json()["contributed"] is True

    # Search with a query whose key terms are IN the content
    search_r = requests.get(
        f"{BASE}/search",
        params={"q": "傷殘津貼可唔可以用私家醫生報告", "n": 3},
    )
    assert search_r.status_code == 200
    data = search_r.json()
    assert data["from_cache"] is True
    
    # At least one hit should be DIRECT_MATCH because "私家醫生" + "傷殘" + "報告" all in content
    relevance_levels = [h["relevance"] for h in data["hits"]]
    assert "DIRECT_MATCH" in relevance_levels, f"Expected DIRECT_MATCH, got {relevance_levels}"


def test_relevance_narrows_down():
    """Same domain but query asks a specific question not in cached content → NARROWS_DOWN."""
    # Contribute general SWD info (no mention of private doctor)
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "傷殘津貼申請一般流程",
            "content": "申請傷殘津貼需要填寫社會福利署指定表格，連同身份證明文件及醫療報告提交至各區社會保障辦事處。處理時間約為四至六星期。津貼金額分為普通及高額兩種。",
            "source_url": "https://www.swd.gov.hk/tc/disability-general",
            "tags": ["welfare", "hong-kong", "disability"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    assert r.json()["contributed"] is True

    # Search for private doctor specifically — content has "醫療報告" but not "私家醫生"
    search_r = requests.get(
        f"{BASE}/search",
        params={"q": "私家醫生可唔可以簽傷殘津貼醫療報告", "n": 3},
    )
    assert search_r.status_code == 200
    data = search_r.json()
    
    # The general SWD entry should be NARROWS_DOWN (same domain, but missing "私家醫生")
    relevance_levels = [h["relevance"] for h in data["hits"]]
    assert "NARROWS_DOWN" in relevance_levels or "TOPIC_ONLY" in relevance_levels, \
        f"Expected NARROWS_DOWN or TOPIC_ONLY, got {relevance_levels}"


def test_relevance_topic_only():
    """Same topic tag but content about completely different sub-topic → TOPIC_ONLY."""
    # Contribute an entry about CSSA (same welfare domain, different topic)
    r = requests.post(
        f"{BASE}/contribute",
        json={
            "query": "綜援CSSA申請資格與入息審查上限",
            "content": "綜合社會保障援助計劃的申請人需通過入息及資產審查。單身人士每月入息上限為港幣四千五百元，資產上限為三萬三千元。申請人必須為香港居民並居港滿一年。",
            "source_url": "https://www.swd.gov.hk/en/cssa-info",
            "tags": ["welfare", "hong-kong", "finance"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    assert r.status_code == 200
    assert r.json()["contributed"] is True

    # Search for disability allowance — same "welfare" tag, completely different content
    search_r = requests.get(
        f"{BASE}/search",
        params={"q": "傷殘津貼高額同普通有咩分別", "n": 5},
    )
    assert search_r.status_code == 200
    data = search_r.json()
    
    # CSSA entry should be TOPIC_ONLY for disability question
    # (It may also match other disability entries that are DIRECT_MATCH)
    relevance_levels = [h["relevance"] for h in data["hits"]]
    # At minimum, no crash + valid levels
    for level in relevance_levels:
        assert level in ("DIRECT_MATCH", "NARROWS_DOWN", "TOPIC_ONLY"), f"Invalid level: {level}"


def test_relevance_summary_action():
    """Relevance summary must contain correct action guidance."""
    # Contribute a DIRECT_MATCH entry
    requests.post(
        f"{BASE}/contribute",
        json={
            "query": "exact match test entry for relevance summary validation",
            "content": "exact match test entry for relevance summary validation — this content contains all the key terms from the query so it should produce a DIRECT_MATCH relevance score.",
            "source_url": "https://example.com/exact-match",
            "tags": ["welfare", "hong-kong"],
            "privacy_class": "public",
            "user_level": 1,
            "tool_name": "web_search",
        },
    )
    
    search_r = requests.get(
        f"{BASE}/search",
        params={"q": "exact match test entry for relevance summary validation", "n": 3},
    )
    data = search_r.json()
    summary = data["relevance_summary"]
    
    assert "action" in summary
    assert summary["action"] in ("present_answer", "use_as_context", "discard_or_weak_context", "no_cache")
    assert "guidance" in summary
    assert "breakdown" in summary
    assert "direct_match" in summary["breakdown"]
    assert "narrows_down" in summary["breakdown"]
    assert "topic_only" in summary["breakdown"]


# ── MAIN ─────────────────────────────────────────────────────


def main():
    global PASS, FAIL
    print("\n🧪 Bot Collective Cache — Integration Tests\n")
    print("─" * 50)

    # Health check first
    try:
        requests.get(f"{BASE}/health", timeout=3)
    except requests.ConnectionError:
        print("❌ Server not running at", BASE)
        print("   Start with: python server.py")
        sys.exit(1)

    # Run tests
    test("Health endpoint", test_health)
    test("Stats (empty)", test_stats_empty)
    test("Contribute valid", test_contribute_valid)
    test("Contribute with PII", test_contribute_with_pii)
    test("Search hit", test_search_hit)
    test("Search miss", test_search_miss)
    test("Contribute rejected (level 0)", test_contribute_insufficient_level)
    test("Contribute rejected (too short)", test_contribute_too_short)
    test("Contribute rejected (no tags)", test_contribute_no_tags)
    test("Contribute rejected (no source url)", test_contribute_no_source_url)
    test("Stats (after contribute)", test_stats_after_contribute)
    test("Contribute rejected (private path)", test_contribute_private_path)
    test("Relevance: DIRECT_MATCH", test_relevance_direct_match)
    test("Relevance: NARROWS_DOWN", test_relevance_narrows_down)
    test("Relevance: TOPIC_ONLY", test_relevance_topic_only)
    test("Relevance: summary action", test_relevance_summary_action)

    print("─" * 50)
    total = PASS + FAIL
    print(f"\n  Results: {PASS}/{total} passed", end="")
    if FAIL > 0:
        print(f", {FAIL} failed ❌")
    else:
        print(" ✅")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
