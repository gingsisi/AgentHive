#!/usr/bin/env python3
"""
Bot Collective Capture Receiver — Server-side Layer 3 PII re-filtering.
Accepts captures from the Human Bridge Chrome Extension.
Re-scans for PII, strips, then contributes to ChromaDB.
"""

import json
import re
import sys
import os
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path

# ── Layer 3 PII Patterns (server-side, more aggressive) ──
SERVER_PII_PATTERNS = [
    # Email
    (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', re.IGNORECASE), '[EMAIL]'),
    
    # Phone numbers (global)
    (re.compile(r'(\+852[\s-]?\d{8})|(\+86[\s-]?\d{11})|(\+81[\s-]?\d{2,4}[\s-]?\d{2,4}[\s-]?\d{4})|(\+1[\s-]?\d{10})|(\+44[\s-]?\d{10})|(\+886[\s-]?\d{9,10})|(\+65[\s-]?\d{8})|(\+82[\s-]?\d{9,11})|(\+?\d{1,3}[\s-]?\d{4,}[\s-]?\d{4,})'), '[PHONE]'),
    
    # Credit cards
    (re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'), '[CARD]'),
    
    # ── National IDs ──
    (re.compile(r'[A-Z]\d{6}\(\d\)', re.IGNORECASE), '[HKID]'),
    (re.compile(r'\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b'), '[ID_CN]'),
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[SSN_US]'),
    (re.compile(r'\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b'), '[NI_UK]'),
    (re.compile(r'\b[A-Z][12]\d{8}\b'), '[ID_TW]'),
    (re.compile(r'\b[STFG]\d{7}[A-Z]\b'), '[NRIC_SG]'),
    (re.compile(r'\b\d{6}-\d{7}\b'), '[RRN_KR]'),
    (re.compile(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b'), '[CPF_BR]'),
    
    # Passport numbers
    (re.compile(r'(?:passport|護照|护照|パスポート)(?:\s*(?:no|number|num|#))?\s*[:：]?\s*[A-Z0-9]{6,14}', re.IGNORECASE), '[PASSPORT]'),
    
    # Names after common patterns
    (re.compile(r'(氏名|名前|姓名|name)[:：\s]*[^\n]{2,20}', re.IGNORECASE), '[NAME]'),
    # Addresses
    (re.compile(r'〒\d{3}[-]\d{4}[^\n]*'), '[ADDRESS]'),
    # IP addresses
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '[IP]'),
    # Dates of birth
    (re.compile(r'\b(19|20)\d{2}[年/-]\d{1,2}[月/-]\d{1,2}[日]?\b'), '[DOB]'),
]

# ── Content quality patterns ──
LOW_QUALITY_PATTERNS = [
    (re.compile(r'^\s*(cookie|privacy|terms|同意|クッキー)\s*$', re.IGNORECASE | re.MULTILINE), 'cookie_banner'),
    (re.compile(r'^(loading|読み込み中|loading\.{3})\s*$', re.IGNORECASE), 'loading_state'),
    (re.compile(r'^\s*$'), 'empty'),
]


def strip_pii_server(content: str) -> tuple[str, list[str]]:
    """
    Server-side PII stripping. More aggressive than the extension.
    Returns (cleaned_content, list_of_what_was_stripped).
    """
    stripped = []
    for pattern, replacement in SERVER_PII_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            stripped.append(f"{replacement}:{len(matches)}")
            content = pattern.sub(replacement, content)
    return content, stripped


def check_content_quality(content: str) -> tuple[bool, str]:
    """
    Check if captured content is worth contributing.
    Returns (is_good, reason).
    """
    if not content or len(content.strip()) < 50:
        return False, 'too_short'
    
    for pattern, reason in LOW_QUALITY_PATTERNS:
        if pattern.match(content.strip()):
            return False, reason
    
    # Check for substantial text 
    # Split by both newlines AND sentence endings (for Japanese/Chinese text)
    import re
    sentences = re.split(r'[\n。！？!?]', content)
    substantial = [s.strip() for s in sentences if len(s.strip()) > 10]
    
    if len(substantial) < 3 and len(content.strip()) < 150:
        return False, 'not_enough_substantial_text'
    
    return True, 'ok'


def process_capture(capture_data: dict) -> dict:
    """
    Full server-side processing pipeline for a human-bridge capture.
    
    Returns status dict:
      { contributed: bool, pii_stripped: list, quality_pass: bool, item_id: str, notes: str }
    """
    content = capture_data.get('text', capture_data.get('content', ''))
    url = capture_data.get('source_url', capture_data.get('url', ''))
    query = capture_data.get('query', capture_data.get('title', url))
    tags = capture_data.get('tags', ['human-bridge'])
    
    # Layer 3: PII re-scan
    clean_content, pii_stripped = strip_pii_server(content)
    
    # Quality check
    quality_ok, quality_reason = check_content_quality(clean_content)
    
    if not quality_ok:
        return {
            'contributed': False,
            'pii_stripped': pii_stripped,
            'quality_pass': False,
            'quality_reason': quality_reason,
            'item_id': '',
            'notes': f'Rejected: {quality_reason}'
        }
    
    # If PII was stripped, log it
    if pii_stripped:
        log_path = Path(__file__).parent / 'captures' / 'pii_stripped.log'
        log_path.parent.mkdir(exist_ok=True)
        with open(log_path, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {url}\n")
            f.write(f"  Stripped: {', '.join(pii_stripped)}\n\n")
    
    # Return cleaned data for contribution
    return {
        'contributed': True,
        'pii_stripped': pii_stripped,
        'quality_pass': True,
        'quality_reason': 'ok',
        'item_id': '',  # Will be set after ChromaDB insertion
        'clean_content': clean_content,
        'clean_query': query,
        'notes': f'PII stripped: {len(pii_stripped)} patterns' if pii_stripped else 'Clean'
    }


# ── CLI Mode ──
if __name__ == '__main__':
    # Test with sample data
    test = {
        'url': 'https://tabelog.com/test',
        'title': 'Test Restaurant',
        'text': 'Great ramen shop. Contact chef@test.com or call 090-1234-5678',
        'tags': ['human-bridge', 'test']
    }
    result = process_capture(test)
    print(json.dumps(result, indent=2, ensure_ascii=False))
