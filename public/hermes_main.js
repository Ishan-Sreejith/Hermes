import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.4.0/firebase-app.js';
import {
  getDatabase,
  ref,
  push,
  set,
  onChildAdded,
  onValue,
  onDisconnect,
  serverTimestamp,
  query,
  orderByChild,
  limitToLast,
  get,
} from 'https://www.gstatic.com/firebasejs/12.4.0/firebase-database.js';
import { getAuth, signInAnonymously } from 'https://www.gstatic.com/firebasejs/12.4.0/firebase-auth.js';

/**
 * HERMES WEB CLIENT - Premium / OLED v7 (CLI Interop + Peer Search)
 * Aligned paths and payloads for full CLI compatibility.
 */

const HERMES_VERSION = "7.0.0-CLI-SYNC";

const FIREBASE_CONFIG = {
  apiKey: "AIzaSyAz-dD-6bXh3-gR8g_H_r6Q-vY-rQ",
  authDomain: "hermes-1bcc8.firebaseapp.com",
  databaseURL: "https://hermes-1bcc8-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "hermes-1bcc8",
  storageBucket: "hermes-1bcc8.firebasestorage.app",
  messagingSenderId: "400185148743",
  appId: "1:400185148743:web:8689241bc30d9cb6797862"
};

const state = {
  app: null, db: null, auth: null,
  userId: localStorage.getItem('hermesUserId') || null,
  username: localStorage.getItem('hermesUsername') || '',
  activeTarget: localStorage.getItem('hermesActiveTarget') || '@broadcast',
  activeChats: new Set(['@broadcast', '@dev', '@general']),
  peers: [],
  channels: new Set(['@broadcast', '@dev', '@general']),
  unsubs: { messages: null, presence: null, sync: null },
  seenKeys: new Set(),
  lastSenderId: null,
  firebaseReady: false
};

const el = {
  authOverlay: document.getElementById('auth-overlay'),
  setupUsername: document.getElementById('setup-username'),
  setupBtn: document.getElementById('setup-btn'),
  chatThread: document.getElementById('chat-thread'),
  messageInput: document.getElementById('message-input'),
  sendBtn: document.getElementById('send-btn'),
  channelList: document.getElementById('channel-list'),
  directList: document.getElementById('direct-list'),
  displayName: document.getElementById('display-name'),
  activeTargetDisplay: document.getElementById('active-target-display'),
  profileBtn: document.getElementById('user-profile-btn'),
  discoveryOverlay: document.getElementById('discovery-overlay'),
  discoveryList: document.getElementById('discovery-list'),
  discoverySearch: document.getElementById('discovery-search'),
};

// --- Utilities ---

function saveLocal() {
  localStorage.setItem('hermesActiveTarget', state.activeTarget);
  localStorage.setItem('hermesUsername', state.username);
  localStorage.setItem('hermesUserId', state.userId);
}

function safeKey(value) { return String(value || '').replace(/[.#$/\[\]]/g, '_'); }
function fmtTs(tsSec) {
  const d = new Date((tsSec || Date.now() / 1000) * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// --- Layout & Rendering ---

function renderSysLine(text, cls = '') {
  const div = document.createElement('div');
  div.className = `sys-msg ${cls}`;
  div.textContent = text;
  el.chatThread.appendChild(div);
  el.chatThread.scrollTop = el.chatThread.scrollHeight;
  state.lastSenderId = null; 
}

function renderMessage(msg) {
  const id = msg.id || `${msg.ts}-${msg.fromId}`;
  if (state.seenKeys.has(id)) return;
  state.seenKeys.add(id);

  // CLI Interop naming (prioritize body/from_id)
  const fromId = msg.from_id || msg.fromId || 'unknown';
  const fromName = msg.from_name || msg.fromName || fromId;
  const body = msg.body || msg.text || '';
  const ts = fmtTs(msg.ts);
  const isMe = fromId === state.userId;

  if (!isMe && !fromId.startsWith('@') && !state.activeChats.has(fromId)) addActiveChat(fromId);

  const lastGroup = el.chatThread.lastElementChild;
  if (lastGroup && lastGroup.classList.contains('message-group') && lastGroup.getAttribute('data-from') === fromId) {
      const textDiv = document.createElement('div');
      textDiv.className = 'message-text';
      textDiv.textContent = body;
      
      const tsDiv = document.createElement('div');
      tsDiv.className = 'message-ts';
      tsDiv.textContent = ts;
      
      lastGroup.appendChild(textDiv);
      lastGroup.appendChild(tsDiv);
      el.chatThread.scrollTop = el.chatThread.scrollHeight;
      return;
  }

  const group = document.createElement('div');
  group.className = `message-group ${isMe ? 'me' : ''}`;
  group.setAttribute('data-from', fromId);
  
  group.innerHTML = `
    <div class="message-header">
      ${!isMe ? `<div class="msg-avatar">${fromName.charAt(0).toUpperCase()}</div>` : ''}
      <div class="msg-username">${fromName}</div>
    </div>
    <div class="message-text">${body}</div>
    <div class="message-ts">${ts}</div>
  `;

  el.chatThread.appendChild(group);
  el.chatThread.scrollTop = el.chatThread.scrollHeight;
  state.lastSenderId = fromId;
}

function renderSidebar() {
  el.channelList.innerHTML = '';
  state.channels.forEach(name => {
    const li = document.createElement('li');
    li.className = `sidebar-item ${state.activeTarget === name ? 'active' : ''}`;
    li.innerHTML = `<span class="icon">#</span><span>${name}</span>`;
    li.onclick = () => switchTarget(name);
    el.channelList.appendChild(li);
  });

  el.directList.innerHTML = '';
  const activePeers = state.peers.filter(p => state.activeChats.has(p.id));
  activePeers.forEach(peer => {
    const li = document.createElement('li');
    li.className = `sidebar-item online ${state.activeTarget === peer.id ? 'active' : ''}`;
    li.innerHTML = `<div class="status-dot"></div><span>${peer.name || peer.id}</span>`;
    li.onclick = () => switchTarget(peer.id);
    el.directList.appendChild(li);
  });
}

async function addActiveChat(id) {
    if (!id || id.startsWith('@')) return;
    state.activeChats.add(id);
    if (state.db && state.userId) set(ref(state.db, `users/${safeKey(state.userId)}/active_chats/${safeKey(id)}`), true);
    renderSidebar();
}

async function switchTarget(target) {
  if (state.unsubs.messages) state.unsubs.messages();
  state.activeTarget = target;
  el.activeTargetDisplay.textContent = target;
  el.chatThread.innerHTML = '';
  state.seenKeys.clear();
  state.lastSenderId = null;
  saveLocal();
  renderSidebar();
  
  if (state.firebaseReady) {
    // Aligned with CLI: @broadcast uses messages/broadcast
    const path = target === '@broadcast' ? 'messages/broadcast' 
      : (target.startsWith('@') ? `messages/chan_${safeKey(target.slice(1).toLowerCase())}` : `messages/${safeKey(target)}`);
    
    try {
        const q = query(ref(state.db, path), orderByChild('ts'), limitToLast(80));
        const snap = await get(q);
        if (snap.exists()) {
          const items = Object.values(snap.val()).sort((a,b) => a.ts - b.ts);
          items.forEach(renderMessage);
        }
        state.unsubs.messages = onChildAdded(q, (s) => renderMessage(s.val()));
    } catch (e) { console.error("History fail:", e); }
  }
}

// --- Peer Discovery & Search ---

function renderDiscovery(filter = '') {
    el.discoveryList.innerHTML = '';
    const term = filter.toLowerCase();
    
    const filtered = state.peers.filter(p => 
        p.name.toLowerCase().includes(term) || p.id.toLowerCase().includes(term)
    );
    
    filtered.forEach(p => {
        const li = document.createElement('li');
        li.className = 'modal-item';
        li.innerHTML = `
            <div class="msg-avatar">${p.name.charAt(0).toUpperCase()}</div>
            <div>
                <div style="font-weight:600">${p.name}</div>
                <div style="font-size:11px; color:#888">${p.id}</div>
            </div>
        `;
        li.onclick = () => { addActiveChat(p.id); switchTarget(p.id); closeDiscovery(); };
        el.discoveryList.appendChild(li);
    });

    if (filtered.length === 0) {
        el.discoveryList.innerHTML = `<div style="text-align:center; padding:20px; color:#666">No peers matching "${filter}"</div>`;
    }
}

function openDiscovery() {
    el.discoveryOverlay.style.display = 'flex';
    el.discoverySearch.value = '';
    renderDiscovery();
    el.discoverySearch.focus();
}

function closeDiscovery() { el.discoveryOverlay.style.display = 'none'; }

// --- Firebase ---

async function sendMessage(text) {
  if (!text || !state.firebaseReady || !state.db) return;
  if (text.startsWith('/')) return handleCommand(text);

  if (!state.activeTarget.startsWith('@')) addActiveChat(state.activeTarget);

  // CLI Interop: Populate all key variations
  const payload = {
    v: 2, id: crypto.randomUUID(), type: 'msg',
    fromId: state.userId, from_id: state.userId,
    fromName: state.username, from_name: state.username,
    to: state.activeTarget, scope: state.activeTarget.startsWith('@') ? 'public' : 'private',
    text: text, body: text, 
    ts: Date.now() / 1000, 
    source: 'hermes-web', 
    enc: 'none'
  };

  const path = state.activeTarget === '@broadcast' ? 'messages/broadcast'
    : (state.activeTarget.startsWith('@') ? `messages/chan_${safeKey(state.activeTarget.slice(1).toLowerCase())}` : `messages/${safeKey(state.activeTarget)}`);

  try { 
      await push(ref(state.db, path), payload); 
  } catch (e) { 
      renderSysLine(`Send failed: ${e.message}`, 'error'); 
  }
}

function handleCommand(raw) {
    const parts = raw.split(' ');
    const cmd = parts[0].toLowerCase();
    if (cmd === '/name') {
        const name = parts.slice(1).join(' ');
        if (!name) return renderSysLine('Usage: /name <username>', 'error');
        state.username = name; el.displayName.textContent = name;
        saveLocal();
        if (state.db) set(ref(state.db, `presence/${safeKey(state.userId)}/name`), name);
        renderSysLine(`Identity set: ${name}`, 'success');
    } else if (cmd === '/clear') { el.chatThread.innerHTML = ''; }
      else if (cmd === '/theme') { document.body.classList.toggle('light-mode'); }
      else { renderSysLine(`Unknown: ${cmd}`, 'error'); }
}

async function connectFirebase() {
  renderSysLine('Synchronizing Hermes Link...', 'sys');
  try {
    state.app = initializeApp(FIREBASE_CONFIG);
    state.db = getDatabase(state.app);
    state.auth = getAuth(state.app);
    await signInAnonymously(state.auth);
    state.firebaseReady = true;
    
    onValue(ref(state.db, `users/${safeKey(state.userId)}/active_chats`), (s) => {
        if (s.exists()) {
            Object.keys(s.val()).forEach(id => state.activeChats.add(id));
            renderSidebar();
        }
    });

    const pRef = ref(state.db, `presence/${safeKey(state.userId)}`);
    set(pRef, { id: state.userId, name: state.username, online: true, updatedAt: serverTimestamp() });
    onDisconnect(pRef).remove();

    onValue(ref(state.db, 'presence'), (snap) => {
      const val = snap.val() || {};
      state.peers = Object.values(val).filter(p => p && p.id && p.id !== state.userId && p.online);
      renderSidebar();
      if (el.discoveryOverlay.style.display === 'flex') renderDiscovery(el.discoverySearch.value);
    });

    await switchTarget(state.activeTarget);
    renderSysLine('Firebase Uplink established.', 'success');
  } catch (err) {
    console.error(err);
    renderSysLine(`Uplink Error: ${err.message}`, 'error');
    state.firebaseReady = true;
    await switchTarget(state.activeTarget);
  }
}

window.onload = () => {
    console.log("HERMES_BOOT:", HERMES_VERSION);
    if (!state.userId) el.authOverlay.style.display = 'flex';
    else { el.displayName.textContent = state.username; connectFirebase(); }
    el.setupBtn.onclick = () => {
        const input = el.setupUsername.value.trim();
        if (!input) return;
        state.username = input; state.userId = crypto.randomUUID();
        saveLocal(); el.authOverlay.style.display = 'none';
        el.displayName.textContent = state.username; connectFirebase();
    };
    el.sendBtn.onclick = () => { sendMessage(el.messageInput.value); el.messageInput.value = ''; };
    el.messageInput.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el.sendBtn.click(); } };
    el.profileBtn.onclick = () => { const n = prompt('New username:', state.username); if (n) handleCommand(`/name ${n}`); };
    el.discoverySearch.oninput = (e) => renderDiscovery(e.target.value);
};
window.openDiscovery = openDiscovery; window.closeDiscovery = closeDiscovery;
