// Background Service Worker — Icon management & state sync
// Minimal, no external API dependencies beyond storage + tabs.

let isEnabled = false;
let isWhitelistedPage = false;

// ── Init from storage ──
chrome.storage.local.get('botbridge_state', (result) => {
  const state = result.botbridge_state || {};
  isEnabled = state.enabled || false;
  updateIcon();
});

// ── Listen for toggle changes from popup ──
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'toggle') {
    isEnabled = msg.enabled;
    updateIcon();
    sendResponse({ ok: true });
  }
});

// ── Tab activation → check if current page is whitelisted ──
chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    if (tab.url && tab.url.startsWith('http')) {
      const url = new URL(tab.url);
      const result = await chrome.storage.local.get('botbridge_state');
      const state = result.botbridge_state || {};
      const whitelist = state.whitelist || [];
      isWhitelistedPage = whitelist.some(d => url.hostname.includes(d));
      isEnabled = state.enabled || false;
      updateIcon();
    }
  } catch (e) {
    // Ignore chrome://, edge://, etc.
  }
});

// ── Tab URL change (SPA navigation) ──
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.url && changeInfo.url.startsWith('http')) {
    try {
      const url = new URL(changeInfo.url);
      const result = await chrome.storage.local.get('botbridge_state');
      const state = result.botbridge_state || {};
      const whitelist = state.whitelist || [];
      isWhitelistedPage = whitelist.some(d => url.hostname.includes(d));
      isEnabled = state.enabled || false;
      updateIcon();
      
      // Reload content script state
      chrome.tabs.sendMessage(tabId, { 
        type: 'reload_state',
        enabled: isEnabled,
        whitelisted: isWhitelistedPage
      }).catch(() => {}); // Ignore if content script not ready
    } catch (e) {}
  }
});

// ── Icon update ──
function updateIcon() {
  let iconState = 'off';
  if (isEnabled && isWhitelistedPage) {
    iconState = 'on';
  } else if (isEnabled) {
    iconState = 'warn';
  }
  
  chrome.action.setIcon({
    path: {
      '16': `icons/icon-${iconState}-16.png`,
      '48': `icons/icon-${iconState}-48.png`,
      '128': `icons/icon-${iconState}-128.png`
    }
  }).catch(() => {}); // Ignore if icons missing
}
