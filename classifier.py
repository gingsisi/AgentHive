"""
Security & Classification Layer for Bot Collective Cache.
Classifies content before sharing and strips PII.
"""

import re
from enum import Enum
from typing import Tuple


class ContentClass(str, Enum):
    """Classification labels for tool outputs."""

    WEB_RESULT = "web_result"  # Auto-shareable (browser/navigate/search output)
    PRIVATE = "private"  # Never share (memory, personal files)
    MIXED = "mixed"  # Needs review (terminal, delegate_task)
    UNKNOWN = "unknown"  # Default for unclassified


# ── PII PATTERNS ──────────────────────────────────────────────

PII_PATTERNS = [
    # Emails
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[EMAIL]"),
    
    # ── Phone Numbers ──
    (re.compile(r"(\+852[\s-]?)?[2-9]\d{3}[\s-]?\d{4}"), "[PHONE_HK]"),
    (re.compile(r"(\+86[\s-]?)?1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}"), "[PHONE_CN]"),
    (re.compile(r"(\+1[\s-]?)?\d{3}[\s-]?\d{3}[\s-]?\d{4}"), "[PHONE_US]"),
    (re.compile(r"(\+44[\s-]?)?0?7\d{3}[\s-]?\d{6}"), "[PHONE_UK]"),
    (re.compile(r"(\+81[\s-]?)?0[89]0[\s-]?\d{4}[\s-]?\d{4}"), "[PHONE_JP]"),
    (re.compile(r"(\+886[\s-]?)?0?9\d{2}[\s-]?\d{3}[\s-]?\d{3}"), "[PHONE_TW]"),
    (re.compile(r"(\+65[\s-]?)?[689]\d{3}[\s-]?\d{4}"), "[PHONE_SG]"),
    (re.compile(r"(\+82[\s-]?)?01[016789][\s-]?\d{3,4}[\s-]?\d{4}"), "[PHONE_KR]"),
    
    # ── National IDs ──
    # China: 18-digit (6-digit area + 8-digit birth + 3-digit seq + 1 check)
    (re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"), "[ID_CN]"),
    # US SSN: XXX-XX-XXXX
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_US]"),
    # UK National Insurance: AB 12 34 56 C
    (re.compile(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"), "[NI_UK]"),
    # Taiwan: A123456789
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "[ID_TW]"),
    # Singapore NRIC: S/T/F/G + 7 digits + letter
    (re.compile(r"\b[STFG]\d{7}[A-Z]\b"), "[NRIC_SG]"),
    # Japan My Number: 12 digits
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b(?:番号|番號|マイナンバー)?"), "[MYNUMBER_JP]"),
    # South Korea: YYMMDD-XXXXXXX
    (re.compile(r"\b\d{6}-\d{7}\b"), "[RRN_KR]"),
    # Australia TFN: XXX XXX XXX
    (re.compile(r"\b\d{3}\s?\d{3}\s?\d{3}\b(?:[\s-]?(?:tfn|tax.file.number))?", re.I), "[TFN_AU]"),
    # Canada SIN: XXX-XXX-XXX
    (re.compile(r"\b\d{3}-\d{3}-\d{3}\b"), "[SIN_CA]"),
    # France INSEE: 1M/2M + YYMM + 5 digits
    (re.compile(r"\b[12]\d{2}(?:0[1-9]|1[0-2])\d{5}\b"), "[INSEE_FR]"),
    # Germany: no universal national ID number (datenschutz), skip.
    # India Aadhaar: 12 digits (often spaced)
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[AADHAAR_IN]"),
    # Brazil CPF: XXX.XXX.XXX-XX
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[CPF_BR]"),
    
    # ── Hong Kong ──
    (re.compile(r"\b[A-Z]\d{6}\(\d\)\b"), "[HKID]"),
    
    # ── Passport Numbers ──
    (re.compile(r"(?:passport|護照|护照|パスポート)(?:\s*(?:no|number|num|#))?\s*[:：]?\s*[A-Z0-9]{6,14}", re.I), "[PASSPORT]"),
    
    # ── IP Addresses ──
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    
    # ── API Keys & Tokens ──
    (re.compile(r"(?:api[_-]?key|token|secret|auth[_-]?token)[\s:=]+['\"]?[\w.-]{20,}['\"]?", re.I), "[CREDENTIAL]"),
    
    # ── Credit Cards ──
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[CARD]"),
]


def classify_tool_output(tool_name: str, file_path: str = "") -> ContentClass:
    """
    Classify a tool output based on tool name and context.
    Returns ContentClass enum.
    """
    # Browser / web search tools → always web_result
    web_tools = {
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_type", "browser_scroll", "browser_console",
        "browser_get_images", "browser_vision", "browser_back",
        "browser_press", "web_search", "web_extract",
    }
    if tool_name in web_tools:
        return ContentClass.WEB_RESULT

    # Memory → always private
    if tool_name == "memory":
        return ContentClass.PRIVATE

    # File reads → depends on path
    if tool_name in ("read_file", "write_file", "search_files", "patch"):
        private_prefixes = (
            "/opt/data/home/",
            "/root/",
            "~/",
            "/home/",
        )
        abs_path = str(file_path)
        if any(abs_path.startswith(p) for p in private_prefixes):
            return ContentClass.PRIVATE
        # System/tmp paths → mixed
        system_prefixes = ("/tmp/", "/var/", "/etc/", "/opt/data/")
        if any(abs_path.startswith(p) for p in system_prefixes):
            # But /opt/data/home is personal → private
            if abs_path.startswith("/opt/data/home/"):
                return ContentClass.PRIVATE
            return ContentClass.MIXED
        return ContentClass.MIXED

    # Terminal → mixed (could have anything)
    if tool_name == "terminal":
        return ContentClass.MIXED

    # Delegate task → mixed
    if tool_name == "delegate_task":
        return ContentClass.MIXED

    # Send message / user-facing → private
    if tool_name == "send_message":
        return ContentClass.PRIVATE

    return ContentClass.UNKNOWN


def strip_pii(text: str) -> Tuple[str, bool]:
    """
    Remove PII from text. Returns (cleaned_text, had_pii).
    """
    had_pii = False
    cleaned = text
    for pattern, replacement in PII_PATTERNS:
        new_text, count = pattern.subn(replacement, cleaned)
        if count > 0:
            had_pii = True
            cleaned = new_text
    return cleaned, had_pii


def is_safe_to_share(
    tool_name: str, content: str, file_path: str = "", user_level: int = 1
) -> Tuple[bool, str, str]:
    """
    Determine if content is safe to share at the given user level.

    Args:
        tool_name: The tool that produced the content
        content: The text content to potentially share
        file_path: Optional file path for read_file etc.
        user_level: 0=ghost, 1=auto_web, 2=skills, 3=open

    Returns:
        (can_share: bool, classification: str, cleaned_content: str)
    """
    # Level 0: never share anything
    if user_level == 0:
        return False, "private", ""

    classification = classify_tool_output(tool_name, file_path)

    # Level 1: only web_results
    if user_level == 1 and classification != ContentClass.WEB_RESULT:
        return False, classification.value, ""

    # Level 2: web_results + skills
    # (Skills are contributed explicitly, not auto-classified)

    # Level 3: everything except private
    if user_level == 3 and classification == ContentClass.PRIVATE:
        return False, classification.value, ""

    # Strip PII from any content being shared
    cleaned, had_pii = strip_pii(content)
    if had_pii:
        # Log that PII was stripped (in production, log to audit trail)
        pass

    return True, classification.value, cleaned


# ── TOOL NAME MAPPING ────────────────────────────────────────

def normalize_tool_name(name: str) -> str:
    """Normalize Hermes tool names to classification keys."""
    mapping = {
        "web_search2": "web_search",
        "browser_navigate2": "browser_navigate",
        "browser_snapshot2": "browser_snapshot",
    }
    return mapping.get(name, name)
