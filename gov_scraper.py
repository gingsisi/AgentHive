#!/usr/bin/env python3
"""
HK Government FAQ Scraper — extracts Q&A from blocked government sites
and contributes to Bot Collective cache.

Sources:
  - 1823.gov.hk — 30+ service categories with sub-pages
  - immd.gov.hk — 15 FAQ categories (static HTML)
  - (SWD, EDB, TD — TODO)

Usage:
  python3 gov_scraper.py          # Scrape all sources
  python3 gov_scraper.py --dry    # Dry run (no contribution)
  python3 gov_scraper.py --source 1823  # Single source
"""

import requests
import re
import time
import sys
import json
from urllib.parse import urljoin
from bs4 import BeautifulSoup

CACHE_URL = "https://agenthive-production.up.railway.app"
HEADERS = {
    "X-API-Key": "bc_fffe80757e844d0e2547f7bfc14086bb",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}
DELAY = 1.5  # seconds between requests
TIMEOUT = 15

# ── Utility ───────────────────────────────────────────────────────

def fetch(url, timeout=TIMEOUT):
    """Fetch URL and return BeautifulSoup or None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        # Force utf-8
        resp.encoding = resp.apparent_encoding or 'utf-8'
        return BeautifulSoup(resp.text, 'lxml')
    except Exception as e:
        print(f"  ⚠ Fetch failed: {url} — {e}")
        return None

def contribute(entry, dry=False):
    """Contribute a Q&A entry to the cache."""
    if dry:
        print(f"  [DRY] Would contribute: {entry['query'][:60]}...")
        return True
    
    try:
        resp = requests.post(
            f"{CACHE_URL}/contribute",
            json=entry,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            eid = data.get("id", "?")
            print(f"  ✅ {eid[:12]} — {entry['query'][:50]}...")
            return True
        else:
            detail = resp.json().get("detail", resp.text)
            print(f"  ❌ Rejected: {entry['query'][:50]}... — {detail}")
            return False
    except Exception as e:
        print(f"  ❌ Failed: {entry['query'][:50]}... — {e}")
        return False

def build_entry(query, content, source_url, tags, verified=False):
    """Build a properly formatted cache entry."""
    # Ensure content is long enough
    if len(content) < 50:
        return None
    
    return {
        "query": query[:500],
        "content": content[:8000],
        "source_url": source_url,
        "tags": tags,
        "privacy_class": "public",
    }

# ── 1823.gov.hk Extractor ─────────────────────────────────────────

def extract_1823_categories():
    """Discover all 1823 FAQ category URLs."""
    soup = fetch("https://www.1823.gov.hk/tc/faq/service-categories")
    if not soup:
        return []
    
    cats = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/tc/faq/service-categories/' in href and href.count('/') >= 4:
            full = urljoin("https://www.1823.gov.hk", href)
            cats.add(full)
    
    return sorted(cats)

def extract_1823_questions(category_url):
    """Extract FAQ detail page URLs from a category page."""
    soup = fetch(category_url)
    if not soup:
        return []
    
    questions = []
    # Find all icon-desc--detail links
    for a in soup.find_all('a', class_='icon-desc--detail', href=True):
        full = urljoin("https://www.1823.gov.hk", a['href'])
        # Extract question text from title
        title = a.find('p', class_='icon-desc__title')
        q_text = title.get_text(strip=True) if title else ""
        questions.append((full, q_text))
    
    return questions

def extract_1823_answer(detail_url, question_text):
    """Extract answer from 1823 FAQ detail page."""
    soup = fetch(detail_url)
    if not soup:
        return None
    
    # Answer is in inner-content div
    content_div = soup.find('div', class_='inner-content')
    if not content_div:
        return None
    
    # Get all text, skip nav/chatter
    paragraphs = []
    skip_classes = {'breadcrumbs2', 'chat-bubble', 'content_general', 'rte-img'}
    
    for tag in content_div.find_all(['p', 'li', 'div']):
        # Skip nav/chatter
        cls = ' '.join(tag.get('class', []))
        if any(s in cls for s in skip_classes):
            continue
        
        txt = tag.get_text(strip=True)
        # Skip short/UI text
        if not txt or len(txt) < 20:
            continue
        if txt.startswith('我係1823智能助理'):
            continue
        if '1823' in txt and ('應用程式' in txt or '網上表格' in txt):
            continue
        
        paragraphs.append(txt)
    
    if not paragraphs:
        return None
    
    answer = '\n'.join(paragraphs)
    
    # Infer tags from URL
    tags = infer_tags_1823(detail_url)
    
    return build_entry(
        query=question_text,
        content=answer,
        source_url=detail_url,
        tags=tags,
    )

def infer_tags_1823(url):
    """Infer canonical tags from 1823 URL."""
    tags = ["hong-kong", "government"]
    
    mapping = {
        'social-security': 'welfare',
        'disability': 'welfare',
        'allowance': 'welfare',
        'cssa': 'welfare',
        'housing': 'housing',
        'public-rental': 'housing',
        'transport': 'transport',
        'motoring': 'transport',
        'vehicle': 'transport',
        'driving': 'transport',
        'employment': 'employment',
        'education': 'education',
        'immigration': 'immigration',
        'visa': 'immigration',
        'tax': 'tax',
        'rates': 'tax',
        'company': 'business',
        'business': 'business',
        'health': 'health',
        'medical': 'health',
        'environment': 'environment',
        'food': 'environment',
        'building': 'construction',
        'construction': 'construction',
    }
    
    url_lower = url.lower()
    for key, tag in mapping.items():
        if key in url_lower and tag not in tags:
            tags.append(tag)
    
    # Ensure at least 2 canonical tags
    if len(tags) < 2:
        tags.append("general")
    
    return tags[:5]  # max 5 tags

# ── immd.gov.hk Extractor ─────────────────────────────────────────

IMMD_FAQ_CATEGORIES = [
    ("top10_faq", "十大常見問題"),
    ("faq_hkic", "身份證"),
    ("faqroa", "居留權"),
    ("hk-travel-doc", "旅行證件"),
    ("faqnationality", "中國國籍"),
    ("births-deaths-registration", "出生及死亡登記"),
    ("marriage-registration", "婚姻登記"),
    ("imm-clearance", "出入境檢查"),
    ("online_service", "網上服務"),
    ("enforcement", "執法"),
    ("e_channel_services_for_visitors", "訪港旅客使用e-道服務"),
    ("1868", "1868熱線"),
    ("1868chatbot", "1868聊天機械人"),
    ("others", "其他"),
    ("visas", "簽證／進入許可"),
]

IMMD_BASE = "https://www.immd.gov.hk/hkt/faq/"

def extract_immd_faq(category_slug, category_name):
    """Extract all Q&A from an ImmD FAQ category page."""
    url = f"{IMMD_BASE}{category_slug}.html"
    soup = fetch(url)
    if not soup:
        return []
    
    entries = []
    
    # ImmD uses tab-header (Q) + tab-info (A) pattern
    tab_headers = soup.find_all('div', class_='tab-header')
    
    for header in tab_headers:
        q_text = header.get_text(strip=True)
        if not q_text or len(q_text) < 5:
            continue
        
        # Clean question text (remove "問N：" / "問N :" prefix)
        q_text = re.sub(r'^問\d+\s*[：:]\s*', '', q_text)
        
        # Find answer in sibling tab-info
        tab_content = header.find_parent('div', class_='tab-content')
        if not tab_content:
            continue
        
        tab_info = tab_content.find('div', class_='tab-info')
        if not tab_info:
            continue
        
        # Collect all <p> text
        paragraphs = tab_info.find_all('p')
        answer = '\n'.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 10)
        
        if not answer or len(answer) < 20:
            continue
        
        tags = ["hong-kong", "government", "immigration"]
        entry = build_entry(
            query=q_text,
            content=answer,
            source_url=url,
            tags=tags,
        )
        if entry:
            entries.append(entry)
    
    return entries

# ── Main ───────────────────────────────────────────────────────────

def scrape_1823(dry=False):
    """Scrape all 1823 FAQ pages."""
    print("\n📋 1823.gov.hk — Discovering categories...")
    categories = extract_1823_categories()
    print(f"  Found {len(categories)} categories")
    
    total = 0
    contributed = 0
    
    for cat_url in categories:
        cat_name = cat_url.rstrip('/').split('/')[-1]
        print(f"\n  📂 {cat_name}")
        
        questions = extract_1823_questions(cat_url)
        print(f"     {len(questions)} questions found")
        
        for detail_url, q_text in questions:
            total += 1
            entry = extract_1823_answer(detail_url, q_text)
            if entry:
                if contribute(entry, dry=dry):
                    contributed += 1
            time.sleep(DELAY)
    
    print(f"\n  📊 1823: {contributed}/{total} contributed")
    return contributed

def scrape_immd(dry=False):
    """Scrape all ImmD FAQ pages."""
    print("\n📋 immd.gov.hk — Processing FAQ categories...")
    
    total = 0
    contributed = 0
    
    for slug, name in IMMD_FAQ_CATEGORIES:
        print(f"\n  📂 {name} ({slug})")
        entries = extract_immd_faq(slug, name)
        print(f"     {len(entries)} Q&A pairs")
        
        for entry in entries:
            total += 1
            if contribute(entry, dry=dry):
                contributed += 1
        time.sleep(DELAY)
    
    print(f"\n  📊 ImmD: {contributed}/{total} contributed")
    return contributed

def check_cache_stats():
    """Check current cache stats."""
    try:
        resp = requests.get(f"{CACHE_URL}/stats", timeout=5)
        if resp.status_code == 200:
            stats = resp.json()
            wc = stats.get("web_cache", "?")
            print(f"\n📊 Cache: {wc} entries in web_cache")
    except:
        print("\n⚠ Cache server not reachable")

if __name__ == "__main__":
    dry = "--dry" in sys.argv
    source = None
    for arg in sys.argv[1:]:
        if arg.startswith("--source="):
            source = arg.split("=")[1]
    
    if dry:
        print("🔍 DRY RUN — no contributions will be made\n")
    
    check_cache_stats()
    
    total_done = 0
    
    if source in (None, "1823"):
        total_done += scrape_1823(dry=dry)
    
    if source in (None, "immd"):
        total_done += scrape_immd(dry=dry)
    
    print(f"\n{'─'*50}")
    print(f"🏁 DONE: {total_done} entries contributed")
    check_cache_stats()
