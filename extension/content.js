// Content Script — Captures page content with PII detection
// Injected into every page. Only captures when extension is ON.

let extensionEnabled = false;
let whitelistDomains = [];
let strictPII = true;
const CAPTURE_DELAY = 1500; // Wait for dynamic content to load

// ── PII Patterns ──
const PII_REGEX = [
  { name: 'Email', re: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/gi },
  { name: 'Phone', re: /(\+852[\s-]?\d{8})|(\+81[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{4})|(0\d{1,3}[\s-]\d{3,4}[\s-]\d{4})/g },
  { name: 'CreditCard', re: /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/g },
  { name: 'HKID', re: /[A-Z]\d{6}\(\d\)/gi },
  { name: 'DOB', re: /\b\d{1,2}[\/-]\d{1,2}[\/-](19|20)\d{2}\b/g },
];

// ── Init ──
async function init() {
  const result = await chrome.storage.local.get('botbridge_state');
  const state = result.botbridge_state || {};
  extensionEnabled = state.enabled || false;
  whitelistDomains = state.whitelist || [];
  strictPII = state.strictPII !== false;
  
  if (extensionEnabled && isWhitelisted()) {
    scheduleCapture();
  }
}

function isWhitelisted() {
  const host = window.location.hostname;
  return whitelistDomains.some(domain => host.includes(domain));
}

// ── Listen for toggle changes ──
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'toggle') {
    extensionEnabled = msg.enabled;
    if (extensionEnabled && isWhitelisted()) {
      scheduleCapture();
    }
  }
  if (msg.type === 'capture_now') {
    capturePage(sendResponse);
    return true; // keep channel open for async
  }
  if (msg.type === 'reload_state') {
    init();
    sendResponse({ ok: true });
  }
  if (msg.type === 'get_state') {
    sendResponse({ enabled: extensionEnabled, whitelisted: isWhitelisted() });
  }
});

// ── Scheduler ──
function scheduleCapture() {
  setTimeout(() => capturePage(), CAPTURE_DELAY);
}

// ── Main Capture Logic ──
function capturePage(callback) {
  const url = window.location.href;
  const host = window.location.hostname;
  const title = document.title || '';
  
  // ── Layer 1: URL-level blocking ──
  const urlBlocked = checkURLBlocked(url);
  if (urlBlocked) {
    const result = { 
      captured: false, 
      blocked: true, 
      reason: `URL blocked: ${urlBlocked}` 
    };
    if (callback) callback(result);
    return;
  }
  
  // Extract meaningful text
  const text = extractPageText();
  
  // ── Layer 1: Content-level PII scanning ──
  const piiHits = scanForPII(text);
  
  if (piiHits.length > 0 && strictPII) {
    const result = { 
      captured: false, 
      blocked: true, 
      reason: `PII detected: ${piiHits.join(', ')}`,
      piiFound: piiHits 
    };
    if (callback) callback(result);
    
    // Still save as blocked for user review
    saveCapture(url, title, text, host, piiHits, true);
    return;
  }
  
  // ── Capture successful ──
  const snippet = text.substring(0, 500).replace(/\s+/g, ' ');
  const tags = generateTags(host, title, text);
  
  saveCapture(url, title, text, host, piiHits, false);
  
  const result = {
    captured: true,
    blocked: false,
    data: {
      url, title, host,
      text: text.substring(0, 10000), // Limit size
      snippet,
      piiFlags: piiHits,
      tags,
      timestamp: new Date().toISOString()
    }
  };
  
  if (callback) callback(result);
}

// ── URL Blocking ──
function checkURLBlocked(url) {
  const BLOCKED = [
    { pattern: /\/(login|signin|signup|register|auth)\b/i, reason: 'Login page' },
    { pattern: /\/(account|myaccount|profile|settings)\b/i, reason: 'Account page' },
    { pattern: /\/(checkout|payment|billing|invoice|receipt)\b/i, reason: 'Payment page' },
    { pattern: /\/(admin|dashboard|config|setup)\b/i, reason: 'Admin page' },
    { pattern: /(mail\.google|gmail|outlook|yahoo\.com\/mail)/i, reason: 'Email service' },
    { pattern: /(bank|banking|hsbc|hang Seng|boc|dbs|standardchartered)/i, reason: 'Banking site' },
  ];
  
  for (const b of BLOCKED) {
    if (b.pattern.test(url)) return b.reason;
  }
  return null;
}

// ── PII Scanning ──
function scanForPII(text) {
  const hits = [];
  for (const { name, re } of PII_REGEX) {
    const matches = text.match(re);
    if (matches && matches.length > 0) {
      hits.push(`${name}(${matches.length})`);
    }
  }
  return hits;
}

// ── Text Extraction ──
function extractPageText() {
  // Remove script, style, nav, footer, header, noscript
  const clone = document.body.cloneNode(true);
  const removeSelectors = 'script, style, noscript, nav, footer, header, iframe, svg, [role="navigation"], [aria-hidden="true"]';
  clone.querySelectorAll(removeSelectors).forEach(el => el.remove());
  
  // Get text from main content areas first, fall back to body
  const mainContent = clone.querySelector('main, article, [role="main"], .content, .main-content, #content, #main');
  const source = mainContent || clone;
  
  return (source.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
}

// ── Tag Generation ──
function generateTags(host, title, text) {
  const tags = ['human-bridge'];
  
  // Domain-based
  if (host.includes('tabelog')) tags.push('restaurant', 'japan', 'food');
  if (host.includes('jalan') || host.includes('rurubu') || host.includes('rakuten')) tags.push('travel', 'japan');
  if (host.includes('aastocks')) tags.push('stock', 'hk');
  if (host.includes('yahoo.com/finance')) tags.push('finance');
  if (host.includes('1823.gov.hk') || host.includes('immd.gov.hk')) tags.push('hk-gov');
  
  // Content-based hints
  const lower = (title + ' ' + text.substring(0, 500)).toLowerCase();
  if (lower.includes('ramen') || lower.includes('sushi') || lower.includes('restaurant')) tags.push('food');
  if (lower.includes('hotel') || lower.includes('ryokan')) tags.push('accommodation');
  if (lower.includes('train') || lower.includes('station') || lower.includes('railway')) tags.push('transport');
  
  return [...new Set(tags)];
}

// ── Save to pending queue ──
async function saveCapture(url, title, text, host, piiFlags, blocked) {
  const result = await chrome.storage.local.get('botbridge_pending');
  const pending = result.botbridge_pending || [];
  
  // Dedup: skip if same URL already captured in last 10 minutes
  const tenMinAgo = Date.now() - 10 * 60 * 1000;
  const duplicate = pending.find(p => 
    p.url === url && new Date(p.timestamp).getTime() > tenMinAgo
  );
  if (duplicate) {
    console.log('BotBridge: Skipping duplicate URL:', url);
    return;
  }
  pending.push({
    url,
    title,
    host,
    snippet: text.substring(0, 300).replace(/\s+/g, ' '),
    text: text.substring(0, 10000),
    piiFlags,
    blockedReason: blocked ? 'PII detected' : null,
    tags: generateTags(host, title, text),
    timestamp: new Date().toISOString()
  });
  
  // Keep max 50 items
  if (pending.length > 50) pending.shift();
  
  await chrome.storage.local.set({ 'botbridge_pending': pending });
}

// ── Start ──
init();
