"""
Relevance Check Layer for Bot Collective Cache.

Checks whether cached content actually ANSWERS a query,
not just shares the same topic. Returns three levels:

  DIRECT_MATCH — content likely answers the question
  NARROWS_DOWN — related domain, narrows search space
  TOPIC_ONLY   — same topic but unrelated to specific question
"""

import re

# ── Chinese stop words (common particles, pronouns, markers) ───

CN_STOP_WORDS = {
    # Particles / modal
    "嘅", "啊", "呀", "啦", "囉", "嘛", "啩", "喎", "咩", "咋", "卦",
    "呢", "㗎", "啫", "添", "吓", "咋", "噃", "㗎",
    # Demonstratives / pronouns
    "呢個", "嗰個", "邊個", "呢度", "嗰度", "邊度",
    "佢", "佢哋", "我", "我哋", "你", "你哋",
    "乜嘢", "咩嘢", "咁", "噉",
    # Question markers (keep nearby content words)
    "可唔可以", "係咪", "係唔係", "點樣", "點解", 
    "做咩", "做乜", "幾時", "幾多", "邊度", "邊個",
    "點", "點算", "得唔得", "會唔會", "有冇",
    # Common filler
    "咁點", "咁即係", "即係", "其實", "不如",
    "所以", "因為", "不過", "如果", "但係",
    "同埋", "仲有", "之後", "之前", "而家", "而",
    # General
    "個", "啲", "嘅話", "嚟", "去", "會", "要",
    "可以", "應該", "需要", "想", "知道", "了解",
    "幫我", "教", "話", "講", "問",
}

# ── Chinese question markers (used to split query) ───

CN_QUESTION_MARKERS = [
    "可唔可以", "係咪", "係唔係", "點樣", "點解",
    "做咩", "做乜", "點", "得唔得", "會唔會",
    "有冇", "有無", "點算", "可不可以", "是不是",
]

# ── Question-answering response patterns ───

ANSWER_PATTERNS = {
    "zh": [
        # Confirmation / negation
        r"(係|喺|可以|唔可以|唔得|得|正確|對|錯).{0,3}(嘅|㗎|啊)",
        r"(必須|需要|要|唔使|無需|不用).{0,10}(可以|先|至)",
        r"(接受|不接受|承認|不承認|接納|不接納)",
        # Explanation patterns
        r"(原因係|因為|由於|所以|因此)",
        r"(根據|按照|依據).{0,20}(規定|條例|法例|指引)",
        # Quantitative
        r"\d+[%％]", r"\$\d+", r"HK\$\d+", r"\d+\s*(次|日|月|年|歲|個|元|蚊)",
    ],
    "en": [
        r"(yes|no|correct|incorrect|true|false)",
        r"(must|required|need|should|can|cannot|may)",
        r"(according to|per|under|pursuant to)",
        r"(because|therefore|hence|thus)",
        r"\d+[%％]", r"\$\d+",
    ],
}

# ── KEY TERM EXTRACTION ──────────────────────────────────────

def extract_key_terms(query: str) -> list[str]:
    """
    Extract content-bearing terms from a query.
    Strips stop words, question markers, and normalizes.
    
    Returns list of key terms in order of appearance.
    """
    query = query.strip()
    if not query:
        return []
    
    # Step 1: Split on question markers to isolate content blocks
    blocks = _split_on_question_markers(query)
    
    # Step 2: Extract meaningful terms from each block
    terms = []
    for block in blocks:
        block_terms = _extract_from_block(block)
        terms.extend(block_terms)
    
    # Step 3: Deduplicate while preserving order
    seen = set()
    result = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            result.append(t)
    
    return result


def _split_on_question_markers(query: str) -> list[str]:
    """Split query into content blocks, removing question markers."""
    # Build regex for all markers
    escaped = [re.escape(m) for m in CN_QUESTION_MARKERS]
    pattern = "|".join(sorted(escaped, key=len, reverse=True))
    
    if not pattern:
        return [query]
    
    # Split and keep surrounding text
    parts = re.split(f"({pattern})", query)
    
    # Return only non-marker parts (even indices)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:  # Content block
            stripped = part.strip()
            if stripped:
                result.append(stripped)
        # odd indices are the markers themselves — discard
    
    return result if result else [query]


def _extract_from_block(block: str) -> list[str]:
    """Extract meaningful terms from a text block."""
    # Normalize: remove punctuation, extra spaces
    cleaned = re.sub(r'[，。！？、；：""''（）【】《》\s,\.!\?;:\"\'\(\)\[\]]+', ' ', block).strip()
    
    if not cleaned:
        return []
    
    # Try to split into words
    # Chinese: character-level bigrams for compound terms
    # English: word-level
    
    terms = []
    words = cleaned.split()
    
    for word in words:
        word = word.strip()
        if not word:
            continue
        
        # Skip stop words
        if word.lower() in CN_STOP_WORDS:
            continue
        
        # Skip single characters (usually particles)
        if len(word) <= 1 and not word.isascii():
            continue
        
        terms.append(word)
    
    # For pure Chinese (no spaces), extract bigrams
    if not terms and len(cleaned.replace(' ', '')) >= 2:
        pure = cleaned.replace(' ', '')
        for i in range(len(pure) - 1):
            bigram = pure[i:i+2]
            if bigram not in CN_STOP_WORDS:
                terms.append(bigram)
    
    return terms


# ── CONTENT SCANNING ─────────────────────────────────────────

def _count_term_matches(terms: list[str], content: str) -> tuple[int, int, list[str]]:
    """
    Count how many key terms appear in content.
    Returns (matched, total, list_of_matched_terms).
    """
    content_lower = content.lower()
    matched = []
    
    for term in terms:
        if term.lower() in content_lower:
            matched.append(term)
    
    return len(matched), len(terms), matched


def _has_answer_patterns(content: str) -> bool:
    """Check if content contains question-answering patterns."""
    for lang_patterns in ANSWER_PATTERNS.values():
        for pattern in lang_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True
    return False


# ── RELEVANCE CHECK ──────────────────────────────────────────

def check_relevance(query: str, content: str) -> dict:
    """
    Check if cached content answers a specific query.
    
    Returns:
        {
            "level": "DIRECT_MATCH" | "NARROWS_DOWN" | "TOPIC_ONLY",
            "score": 0.0–1.0,
            "explanation": str,
            "matched_terms": [...],
            "total_terms": int,
        }
    """
    if not query or not content:
        return {
            "level": "TOPIC_ONLY",
            "score": 0.0,
            "explanation": "Empty query or content",
            "matched_terms": [],
            "total_terms": 0,
        }
    
    # ── Level 1: Keyword precision check ──
    key_terms = extract_key_terms(query)
    
    if not key_terms:
        return {
            "level": "TOPIC_ONLY",
            "score": 0.0,
            "explanation": "No key terms extracted from query",
            "matched_terms": [],
            "total_terms": 0,
        }
    
    matched_count, total_terms, matched_terms = _count_term_matches(key_terms, content)
    precision = matched_count / total_terms if total_terms > 0 else 0.0
    
    # ── Level 2: Answer pattern boost ──
    has_answer = _has_answer_patterns(content)
    
    # ── Level 3: Quality score ──
    content_len = len(content)
    # Short content is less likely to contain a full answer
    length_bonus = min(content_len / 500, 1.0)  # Max bonus at 500+ chars
    
    # Combined score
    score = (precision * 0.6) + (has_answer * 0.25) + (length_bonus * 0.15)
    
    # ── Determine level ──
    if score >= 0.55 or (precision >= 0.5 and has_answer):
        level = "DIRECT_MATCH"
        explanation = f"Key terms ({matched_count}/{total_terms}) found in content. Likely answers the query."
    elif score >= 0.25 or precision >= 0.25:
        level = "NARROWS_DOWN"
        explanation = f"Partial match ({matched_count}/{total_terms} terms). Same domain, but may not fully answer. {'Answer-like patterns detected.' if has_answer else ''}"
    else:
        level = "TOPIC_ONLY"
        explanation = f"Only {matched_count}/{total_terms} terms matched. Same general topic, but content doesn't address the specific question."
    
    return {
        "level": level,
        "score": round(score, 2),
        "explanation": explanation.strip(),
        "matched_terms": matched_terms,
        "total_terms": total_terms,
    }


# ── BATCH UTILITY ────────────────────────────────────────────

def rank_hits_by_relevance(query: str, hits: list[dict]) -> list[dict]:
    """
    Add relevance info to each hit and re-rank:
    DIRECT_MATCH first, then NARROWS_DOWN, TOPIC_ONLY last.
    Within each tier, sort by relevance score (descending).
    """
    for hit in hits:
        content = hit.get("content", "")
        rel = check_relevance(query, content)
        hit["relevance"] = rel["level"]
        hit["relevance_detail"] = {
            "score": rel["score"],
            "explanation": rel["explanation"],
            "matched_terms": rel["matched_terms"],
            "total_terms": rel["total_terms"],
        }
    
    # Sort: DIRECT_MATCH > NARROWS_DOWN > TOPIC_ONLY, then by score desc
    level_order = {"DIRECT_MATCH": 0, "NARROWS_DOWN": 1, "TOPIC_ONLY": 2}
    hits.sort(key=lambda h: (
        level_order.get(h.get("relevance", "TOPIC_ONLY"), 2),
        -(h.get("relevance_detail", {}).get("score", 0))
    ))
    
    return hits
