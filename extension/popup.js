// Bot Collective Human Bridge — Popup Logic v2
// Robust with error handling for all Chrome API calls.

(function() {
  'use strict';
  
  const STORAGE_KEY = 'botbridge_state';
  const PENDING_KEY = 'botbridge_pending';

    const DEFAULT_STATE = {
    enabled: false,
    termsAgreed: false,
    whitelist: ['tabelog.com', 'jalan.net', 'rurubu.com', 'travel.rakuten.co.jp'],
    strictPII: true,
    captureCount: 0,
    sharedCount: 0
  };

  let currentState = { ...DEFAULT_STATE };
  let pendingItems = [];

  // ── Safe DOM helpers ──
  function $(id) { return document.getElementById(id); }
  function safeText(id, text) { const el = $(id); if (el) el.textContent = text; }

  // ── Init ──
  async function init() {
    // Load state
    try {
      const result = await chrome.storage.local.get(STORAGE_KEY);
      currentState = { ...DEFAULT_STATE, ...(result[STORAGE_KEY] || {}) };
    } catch(e) {
      console.error('Storage load failed:', e);
    }

    // Load pending
    try {
      const result = await chrome.storage.local.get(PENDING_KEY);
      pendingItems = result[PENDING_KEY] || [];
    } catch(e) {
      console.error('Pending load failed:', e);
    }

    renderAll();
    attachListeners();
  }

  // ── Render ──
  function renderAll() {
    // Toggle
    const toggle = $('mainToggle');
    if (toggle) toggle.checked = currentState.enabled;

    // Whitelist
    const wl = $('whitelist');
    if (wl) wl.value = currentState.whitelist.join('\n');

    // Strict PII
    const sp = $('strictPII');
    if (sp) sp.checked = currentState.strictPII;

    // Terms
    const ta = $('termsAgreed');
    if (ta) ta.checked = currentState.termsAgreed;

    updateTermsButton();
    updateStatus();
    renderPendingList();
  }

  function updateStatus() {
    const dot = $('statusDot');
    const text = $('statusText');
    const label = $('toggleLabel');
    
    if (!dot || !text || !label) return;

    if (!currentState.termsAgreed && currentState.enabled) {
      dot.className = 'dot warn';
      text.textContent = 'Accept terms to enable sharing';
      label.textContent = '⚠️';
      label.style.color = '#d29922';
    } else if (currentState.enabled) {
      dot.className = 'dot on';
      text.textContent = 'Active · ' + currentState.captureCount + ' captured';
      label.textContent = 'ON';
      label.style.color = '#3fb950';
    } else {
      dot.className = 'dot off';
      text.textContent = 'Capture paused';
      label.textContent = 'OFF';
      label.style.color = '#f85149';
    }
  }

  function updateTermsButton() {
    const btn = $('acceptTerms');
    const agreed = ($('termsAgreed') || {}).checked;
    if (!btn) return;
    btn.disabled = !agreed;
    btn.style.opacity = agreed ? '1' : '0.5';
  }

  function renderPendingList() {
    const list = $('pendingList');
    const shareBtn = $('shareSelected');
    const deleteBtn = $('deleteAll');
    const countBadge = $('pendingCount');

    if (!list) return;

    if (pendingItems.length === 0) {
      list.innerHTML = '<div class="empty">Nothing pending. Browse whitelisted sites to capture.</div>';
      if (shareBtn) shareBtn.style.display = 'none';
      if (deleteBtn) deleteBtn.style.display = 'none';
      if (countBadge) countBadge.style.display = 'none';
      return;
    }

    let html = '';
    pendingItems.forEach((item, idx) => {
      const flags = (item.piiFlags && item.piiFlags.length > 0)
        ? item.piiFlags.map(f => `<span class="flag pii">⚠️ ${f}</span>`).join(' ')
        : '<span class="flag safe">✅ Clean</span>';
      const blocked = item.blockedReason
        ? `<span class="flag blocked">🛑 ${item.blockedReason}</span>`
        : '';

      html += `<div class="pending-item">
        <div class="url">🌐 ${esc(item.url || '(no url)')}</div>
        <div class="snippet">${esc((item.snippet || '(no text)').substring(0, 200))}</div>
        <div class="flags">${flags} ${blocked}</div>
        <div style="margin-top:4px">
          <input type="checkbox" class="pending-select" data-idx="${idx}" checked>
          <span style="font-size:10px;color:#8b949e">Share</span>
          <button class="btn small danger" data-delidx="${idx}">✕</button>
        </div>
      </div>`;
    });

    list.innerHTML = html;
    if (shareBtn) shareBtn.style.display = 'block';
    if (deleteBtn) deleteBtn.style.display = 'inline-block';
    if (countBadge) { countBadge.style.display = 'inline'; countBadge.textContent = pendingItems.length; }

    // Attach delete button listeners
    list.querySelectorAll('.btn.small.danger').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.delidx);
        pendingItems.splice(idx, 1);
        await chrome.storage.local.set({ [PENDING_KEY]: pendingItems });
        renderPendingList();
      });
    });
  }

  // ── Listeners ──
  function attachListeners() {
    // Toggle
    const toggle = $('mainToggle');
    if (toggle) {
      toggle.addEventListener('change', async () => {
        currentState.enabled = toggle.checked;
        if (currentState.enabled && !currentState.termsAgreed) {
          switchTab('terms');
        }
        await chrome.storage.local.set({ [STORAGE_KEY]: currentState });
        chrome.runtime.sendMessage({ type: 'toggle', enabled: currentState.enabled }).catch(() => {});
        renderAll();
      });
    }

    // Save whitelist
    const saveBtn = $('saveWhitelist');
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        const wl = $('whitelist');
        if (!wl) return;
        currentState.whitelist = wl.value.split('\n').map(s => s.trim()).filter(s => s.length > 0);
        await chrome.storage.local.set({ [STORAGE_KEY]: currentState });
        saveBtn.textContent = '✅ Saved!';
        setTimeout(() => { saveBtn.textContent = '💾 Save'; }, 1200);
      });
    }

    // Capture now
    const capBtn = $('captureNow');
    if (capBtn) {
      capBtn.addEventListener('click', async () => {
        capBtn.textContent = '⏳ Capturing...';
        try {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab) throw new Error('No active tab');
          
          const response = await chrome.tabs.sendMessage(tab.id, { type: 'capture_now' });
          if (response && response.blocked) {
            alert('⚠️ Capture blocked: ' + response.reason);
          } else if (response && response.captured) {
            pendingItems.push(response.data);
            await chrome.storage.local.set({ [PENDING_KEY]: pendingItems });
            currentState.captureCount++;
            await chrome.storage.local.set({ [STORAGE_KEY]: currentState });
            renderAll();
          }
        } catch(e) {
          // Content script may not be loaded, inject it
          try {
            const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
            await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              files: ['content.js']
            });
            // Retry
            const response = await chrome.tabs.sendMessage(tab.id, { type: 'capture_now' });
            if (response && response.captured) {
              pendingItems.push(response.data);
              await chrome.storage.local.set({ [PENDING_KEY]: pendingItems });
              currentState.captureCount++;
              await chrome.storage.local.set({ [STORAGE_KEY]: currentState });
              renderAll();
            } else if (response && response.blocked) {
              alert('⚠️ ' + response.reason);
            }
          } catch(e2) {
            alert('❌ Capture failed: ' + e2.message);
          }
        }
        capBtn.textContent = '📸 Capture This Tab Now';
      });
    }

    // Share selected
    const shareBtn = $('shareSelected');
    if (shareBtn) {
      shareBtn.addEventListener('click', async () => {
        const checked = document.querySelectorAll('.pending-select:checked');
        if (checked.length === 0) { alert('Select at least one item.'); return; }
        
        const selectedIdxs = new Set(Array.from(checked).map(cb => parseInt(cb.dataset.idx)));
        const toShare = [];
        const remaining = [];

        for (let i = 0; i < pendingItems.length; i++) {
          if (selectedIdxs.has(i)) {
            toShare.push(pendingItems[i]);
            currentState.sharedCount++;
          } else {
            remaining.push(pendingItems[i]);
          }
        }

        // Get or prompt for save folder (File System Access API)
        let dirHandle = null;
        try {
          // Try to get stored folder handle from IndexedDB
          dirHandle = await getStoredFolderHandle();
        } catch(e) {}

        if (!dirHandle) {
          try {
            dirHandle = await window.showDirectoryPicker({
              mode: 'readwrite',
              startIn: 'documents'
            });
            await storeFolderHandle(dirHandle);
          } catch(e) {
            if (e.name === 'AbortError') {
              alert('❌ Folder selection cancelled. Share aborted.');
            } else {
              alert('❌ Folder picker not supported. Use Chrome 86+ or set download folder to Google Drive.');
            }
            return;
          }
        }

        // Write share file
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        const filename = `bridge-share-${ts}.json`;
        const content = JSON.stringify(toShare, null, 2);

        try {
          const fileHandle = await dirHandle.getFileHandle(filename, { create: true });
          const writable = await fileHandle.createWritable();
          await writable.write(content);
          await writable.close();

          pendingItems = remaining;
          await chrome.storage.local.set({ [PENDING_KEY]: pendingItems, [STORAGE_KEY]: currentState });
          renderAll();
          alert(`✅ Shared ${toShare.length} item(s) → ${filename}\nSaved to your chosen folder.`);
        } catch(e) {
          alert('❌ Write failed: ' + e.message + '\n\nTry selecting the folder again.');
          await clearStoredFolderHandle();
        }
      });
    }

    // Delete all
    const delBtn = $('deleteAll');
    if (delBtn) {
      delBtn.addEventListener('click', async () => {
        if (confirm('Delete all pending?')) {
          pendingItems = [];
          await chrome.storage.local.set({ [PENDING_KEY]: [] });
          renderAll();
        }
      });
    }

    // Accept terms
    const acceptBtn = $('acceptTerms');
    if (acceptBtn) {
      acceptBtn.addEventListener('click', async () => {
        currentState.termsAgreed = true;
        await chrome.storage.local.set({ [STORAGE_KEY]: currentState });
        acceptBtn.textContent = '✅ Accepted!';
        renderAll();
        setTimeout(() => switchTab('pending'), 500);
      });
    }

    // Terms checkbox
    const termsCb = $('termsAgreed');
    if (termsCb) {
      termsCb.addEventListener('change', updateTermsButton);
    }

    // Strict PII checkbox
    const spCb = $('strictPII');
    if (spCb) {
      spCb.addEventListener('change', async () => {
        currentState.strictPII = spCb.checked;
        await chrome.storage.local.set({ [STORAGE_KEY]: currentState });
      });
    }

    // Reset folder
    const resetBtn = $('resetFolder');
    if (resetBtn) {
      resetBtn.addEventListener('click', async () => {
        await clearStoredFolderHandle();
        alert('📁 Folder reset. Next share will ask you to pick a folder again.');
      });
    }

    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });
  }

  // ── Tab switching ──
  function switchTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    const tab = document.querySelector(`.tab[data-tab="${name}"]`);
    const panel = document.getElementById(`panel-${name}`);
    if (tab) tab.classList.add('active');
    if (panel) panel.classList.add('active');
  }

  // ── Utils ──
  function esc(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── File System Access API helpers (IndexedDB for folder handle) ──
  function openFolderDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open('BotBridgeFolderDB', 1);
      req.onupgradeneeded = () => { req.result.createObjectStore('handles'); };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function storeFolderHandle(handle) {
    const db = await openFolderDB();
    const tx = db.transaction('handles', 'readwrite');
    tx.objectStore('handles').put(handle, 'saveFolder');
    return new Promise(r => { tx.oncomplete = r; });
  }

  async function getStoredFolderHandle() {
    const db = await openFolderDB();
    const tx = db.transaction('handles', 'readonly');
    const req = tx.objectStore('handles').get('saveFolder');
    return new Promise((resolve, reject) => {
      req.onsuccess = () => {
        const handle = req.result;
        if (handle && handle.requestPermission) {
          handle.requestPermission({ mode: 'readwrite' }).then(() => resolve(handle)).catch(() => reject('permission denied'));
        } else {
          reject('no stored handle');
        }
      };
      req.onerror = () => reject(req.error);
    });
  }

  async function clearStoredFolderHandle() {
    const db = await openFolderDB();
    const tx = db.transaction('handles', 'readwrite');
    tx.objectStore('handles').delete('saveFolder');
    return new Promise(r => { tx.oncomplete = r; });
  }

  // ── Listen for incoming captures from content script ──
  chrome.runtime.onMessage.addListener(async (msg, sender) => {
    if (msg.type === 'page_captured' && msg.data) {
      pendingItems.push(msg.data);
      await chrome.storage.local.set({ [PENDING_KEY]: pendingItems });
      currentState.captureCount++;
      await chrome.storage.local.set({ [STORAGE_KEY]: currentState });
      renderAll();
    }
  });

  // ── Start ──
  init();
})();
