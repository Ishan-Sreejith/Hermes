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

const MAX_PER_CHAT = 500;

const state = {
  cfg: null,
  app: null,
  db: null,
  auth: null,
  userId: localStorage.getItem('hermesUserId') || null,
  username: localStorage.getItem('hermesUsername') || '',
  activeTarget: localStorage.getItem('hermesActiveTarget') || '@broadcast',
  autoLoadLimit: 100,
  connected: false,
  firebaseReady: false,
  theme:
    localStorage.getItem('hermesTheme') === 'dark' || localStorage.getItem('hermesTheme') === 'light'
      ? localStorage.getItem('hermesTheme')
      : ((window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'),
  sidebarHidden: localStorage.getItem('hermesSidebarHidden') === '1',
  peers: [],
  peerById: {},
  userDirectory: [],
  usernameById: {},
  chats: new Set(['@broadcast']),
  chatCache: {},
  unread: {},
  currentPath: '',
  chatFilter: '',
  seenByPathKey: new Set(),
  seenMessageIds: new Set(),
  typingIdleTimer: null,
  typingPath: '',
  unsubs: { messages: null, inbox: null, presence: null, connected: null, users: null, typing: null },
};

const el = {
  appRoot: document.getElementById('app-root'),
  sidebar: document.getElementById('sidebar'),
  sidebarStatus: document.getElementById('sidebar-status'),
  chatFilter: document.getElementById('chat-filter'),
  peerSearchInput: document.getElementById('peer-search-input'),
  peerSearchBtn: document.getElementById('peer-search-btn'),
  peerSearchResults: document.getElementById('peer-search-results'),
  chatList: document.getElementById('chat-list'),
  chatTitle: document.getElementById('chat-title'),
  chatMeta: document.getElementById('chat-meta'),
  chatTyping: document.getElementById('chat-typing'),
  chatMessages: document.getElementById('chat-messages'),
  messageInput: document.getElementById('message-input'),
  sendBtn: document.getElementById('send-btn'),
  emptyState: document.getElementById('empty-state'),
  btnBroadcast: document.getElementById('btn-broadcast'),
  btnMenu: document.getElementById('btn-menu'),
  btnNewChat: document.getElementById('btn-new-chat'),
  btnTheme: document.getElementById('btn-theme'),
  btnSettings: document.getElementById('btn-settings'),
  settingsOverlay: document.getElementById('settings-overlay'),
  settingsName: document.getElementById('settings-name'),
  settingsSave: document.getElementById('settings-save'),
  settingsTarget: document.getElementById('settings-target'),
  settingsClose: document.getElementById('settings-close'),
};

function nowSec() {
  return Date.now() / 1000;
}

function formatClock(tsRaw) {
  const d = new Date(Number(tsRaw || nowSec()) * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function toSeconds(tsRaw) {
  const n = Number(tsRaw || 0);
  if (!Number.isFinite(n) || n <= 0) return nowSec();
  return n > 10_000_000_000 ? n / 1000 : n;
}

function safeKey(value) {
  return String(value || '').replace(/[.#$/\[\]]/g, '_');
}

function saveLocal() {
  localStorage.setItem('hermesActiveTarget', state.activeTarget);
  localStorage.setItem('hermesUsername', state.username);
  localStorage.setItem('hermesTheme', state.theme);
  localStorage.setItem('hermesSidebarHidden', state.sidebarHidden ? '1' : '0');
  if (state.userId) localStorage.setItem('hermesUserId', state.userId);
}

function applyTheme(nextTheme) {
  state.theme = nextTheme === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', state.theme);
  saveLocal();
}

function toggleTheme() {
  applyTheme(state.theme === 'dark' ? 'light' : 'dark');
}

function setSidebarHidden(hidden) {
  state.sidebarHidden = !!hidden;
  if (state.sidebarHidden) {
    el.appRoot.classList.add('sidebar-hidden');
  } else {
    el.appRoot.classList.remove('sidebar-hidden');
  }
  saveLocal();
}

function messagePathForTarget(target) {
  if (target === '@broadcast') return 'messages/broadcast';
  if (target.startsWith('@')) return `messages/chan_${safeKey(target.slice(1).toLowerCase())}`;
  return `messages/${safeKey(target)}`;
}

function normalizeTarget(target) {
  const t = String(target || '').trim();
  if (t.startsWith('@')) return `@${t.slice(1).toLowerCase()}`;
  return t;
}

function typingPathForTarget(target) {
  if (target.startsWith('@')) return `typing/chan_${safeKey(target.slice(1).toLowerCase())}`;
  const selfId = String(state.userId || '');
  const other = String(target || '');
  if (!selfId || !other) return `typing/direct_${safeKey(other || 'unknown')}`;
  const parts = [selfId, other].sort();
  return `typing/direct_${safeKey(parts[0])}_${safeKey(parts[1])}`;
}

function statusRank(status) {
  if (status === 'delivered') return 3;
  if (status === 'sent') return 2;
  if (status === 'sending') return 1;
  return 0;
}

function statusGlyph(status) {
  if (status === 'delivered') return 'vv';
  if (status === 'sent') return 'v';
  if (status === 'sending') return '...';
  return '';
}

function displayNameForTarget(target) {
  if (target.startsWith('@')) return target;
  if (target === state.userId) return state.username || 'You';
  return (
    state.usernameById[target]
    || (state.peerById[target] ? state.peerById[target].name : '')
    || `user-${String(target).slice(0, 6)}`
  );
}

function normalizeIncoming(msg) {
  const fromId = String(msg.fromId || msg.from_id || '');
  const fromName = String(msg.fromName || msg.from_name || fromId || 'unknown');
  const enc = String(msg.enc || 'none');
  const bodyRaw = String(msg.text || msg.body || '');
  const body = enc === 'none' ? bodyRaw : `[Encrypted message: ${enc}]`;
  return {
    id: String(msg.id || `${toSeconds(msg.ts)}|${fromId}|${body.slice(0, 64)}`),
    ts: toSeconds(msg.ts),
    fromId,
    fromName,
    to: String(msg.to || ''),
    channel: msg.channel || null,
    body,
    type: String(msg.type || 'msg'),
    enc,
    status: String(msg.status || 'delivered'),
  };
}

function ensureChat(target) {
  if (!state.chatCache[target]) state.chatCache[target] = [];
  state.chats.add(target);
}

function addMessage(target, rawMsg) {
  const msg = normalizeIncoming(rawMsg);
  ensureChat(target);

  const arr = state.chatCache[target];
  const existingIdx = arr.findIndex((m) => m.id === msg.id);
  if (existingIdx >= 0) {
    const old = arr[existingIdx];
    const chosenStatus = statusRank(msg.status) >= statusRank(old.status) ? msg.status : old.status;
    arr[existingIdx] = { ...old, ...msg, status: chosenStatus };
  } else {
    arr.push(msg);
    const dedupeId = `${target}:${msg.id}`;
    state.seenMessageIds.add(dedupeId);
    if (state.seenMessageIds.size > 12000) {
      const first = state.seenMessageIds.values().next().value;
      if (first) state.seenMessageIds.delete(first);
    }
  }
  arr.sort((a, b) => a.ts - b.ts);
  if (arr.length > MAX_PER_CHAT) arr.splice(0, arr.length - MAX_PER_CHAT);

  if (target !== state.activeTarget) {
    state.unread[target] = (state.unread[target] || 0) + 1;
  }

  renderChatList();
  if (target === state.activeTarget) renderMessages();
}

function addSystemMessage(text) {
  addMessage(state.activeTarget, {
    id: `sys-${Date.now()}-${Math.random()}`,
    ts: nowSec(),
    fromId: 'system',
    fromName: 'System',
    body: String(text),
    type: 'system',
  });
}

function chatPreview(target) {
  const arr = state.chatCache[target] || [];
  if (!arr.length) return { text: 'No messages yet', ts: 0 };
  const last = arr[arr.length - 1];
  const fromLabel = last.fromId === state.userId ? 'You' : (state.usernameById[last.fromId] || last.fromName);
  return { text: last.type === 'system' ? last.body : `${fromLabel}: ${last.body}`, ts: last.ts };
}

function chatMatchesFilter(target, filter) {
  if (!filter) return true;
  const f = filter.toLowerCase();
  const name = displayNameForTarget(target).toLowerCase();
  if (name.includes(f)) return true;
  if (String(target || '').toLowerCase().includes(f)) return true;
  const preview = chatPreview(target);
  if (String(preview.text || '').toLowerCase().includes(f)) return true;
  return false;
}

function renderChatList() {
  el.chatList.innerHTML = '';
  const filter = state.chatFilter.trim().toLowerCase();
  const targets = Array.from(state.chats)
    .filter((t) => chatMatchesFilter(t, filter))
    .sort((a, b) => (chatPreview(b).ts || 0) - (chatPreview(a).ts || 0));

  for (const target of targets) {
    const preview = chatPreview(target);
    const li = document.createElement('li');
    li.className = `chat-item${target === state.activeTarget ? ' active' : ''}`;

    const top = document.createElement('div');
    top.className = 'chat-top';
    const name = document.createElement('div');
    name.className = 'chat-name';
    name.textContent = displayNameForTarget(target);
    const time = document.createElement('div');
    time.className = 'chat-time';
    time.textContent = preview.ts ? formatClock(preview.ts) : '';
    top.append(name, time);

    const bottom = document.createElement('div');
    bottom.className = 'chat-bottom';
    const txt = document.createElement('div');
    txt.className = 'chat-preview';
    txt.textContent = preview.text;
    bottom.appendChild(txt);

    const unread = Number(state.unread[target] || 0);
    if (unread > 0) {
      const badge = document.createElement('div');
      badge.className = 'badge';
      badge.textContent = unread > 99 ? '99+' : String(unread);
      bottom.appendChild(badge);
    }

    li.append(top, bottom);
    li.onclick = () => switchTarget(target);
    el.chatList.appendChild(li);
  }
}

function renderMessages() {
  const messages = state.chatCache[state.activeTarget] || [];
  el.chatMessages.innerHTML = '';

  if (messages.length === 0 && el.emptyState) {
    el.chatMessages.appendChild(el.emptyState);
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
    return;
  }

  for (const msg of messages) {
    const row = document.createElement('div');
    const isMe = msg.fromId && msg.fromId === state.userId;
    if (msg.type === 'system') row.className = 'msg-row system';
    else row.className = `msg-row ${isMe ? 'me' : 'them'}`;

    const bubble = document.createElement('div');
    bubble.className = 'bubble';

    if (msg.type !== 'system') {
      const avatar = document.createElement('div');
      avatar.className = `avatar ${isMe ? 'me' : 'them'}`;
      const initials = (isMe ? (state.username || 'Y') : (state.usernameById[msg.fromId] || msg.fromName || 'U')).slice(0, 1).toUpperCase();
      avatar.textContent = initials;
      row.appendChild(avatar);

      const from = document.createElement('div');
      from.className = 'from';
      from.textContent = isMe ? 'You' : (state.usernameById[msg.fromId] || msg.fromName || 'Unknown');
      bubble.appendChild(from);
    }

    const body = document.createElement('div');
    body.className = 'body';
    body.textContent = msg.body;
    bubble.appendChild(body);

    const time = document.createElement('div');
    time.className = 'time';
    const tick = isMe ? statusGlyph(msg.status) : '';
    time.textContent = `${formatClock(msg.ts)}${tick ? ` ${tick}` : ''}`;
    bubble.appendChild(time);

    row.appendChild(bubble);
    el.chatMessages.appendChild(row);
  }

  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

function updateHeader() {
  el.chatTitle.textContent = displayNameForTarget(state.activeTarget);
  el.chatMeta.textContent = state.connected ? 'Connected' : 'Offline';
  el.sidebarStatus.textContent = state.username ? `${state.username} | ${state.connected ? 'online' : 'offline'}` : (state.connected ? 'connected' : 'offline');
}

function outgoingToForTarget(target) {
  return target === '@broadcast' ? '@broadcast' : target;
}

async function loadLatest(limit) {
  if (!state.db) {
    addSystemMessage('Firebase not connected; cannot load messages.');
    return;
  }
  const path = messagePathForTarget(state.activeTarget);
  const q = query(ref(state.db, path), orderByChild('ts'), limitToLast(limit));
  const snap = await get(q);
  if (!snap.exists()) return;

  const values = Object.values(snap.val() || {});
  for (const v of values) addMessage(state.activeTarget, v);
}

async function listenTarget(target) {
  if (!state.db) return;
  if (state.unsubs.messages) state.unsubs.messages();

  const path = messagePathForTarget(target);
  state.currentPath = path;
  const q = query(ref(state.db, path), orderByChild('ts'), limitToLast(state.autoLoadLimit));

  state.unsubs.messages = onChildAdded(q, (snap) => {
    const dedupe = `${path}:${snap.key}`;
    if (state.seenByPathKey.has(dedupe)) return;
    state.seenByPathKey.add(dedupe);
    const msg = snap.val();
    if (!msg || typeof msg !== 'object') return;
    addMessage(target, { ...msg, status: 'delivered' });
  });

  if (state.unsubs.typing) state.unsubs.typing();
  const typingPath = typingPathForTarget(target);
  state.typingPath = typingPath;
  state.unsubs.typing = onValue(ref(state.db, typingPath), (snap) => {
    const data = snap.val() || {};
    const now = nowSec();
    const names = [];
    for (const [uid, info] of Object.entries(data)) {
      if (uid === state.userId) continue;
      if (!info || typeof info !== 'object') continue;
      const ts = toSeconds(info.ts);
      if (now - ts > 6) continue;
      const nm = String(info.name || state.usernameById[uid] || `user-${uid.slice(0, 6)}`);
      names.push(nm);
    }
    if (el.chatTyping) {
      el.chatTyping.textContent = names.length ? `${names.join(', ')} typing...` : '';
    }
  });
}

async function setPresence() {
  if (!state.db || !state.userId || !state.connected) return;
  const presenceRef = ref(state.db, `presence/${safeKey(state.userId)}`);
  await set(presenceRef, {
    id: state.userId,
    name: state.username,
    online: true,
    public: true,
    updatedAt: serverTimestamp(),
    transport: 'firebase-web',
  });
  onDisconnect(presenceRef).remove();
}

async function switchTarget(target) {
  target = normalizeTarget(target);
  if (!target) return;
  ensureChat(target);
  state.activeTarget = target;
  state.unread[target] = 0;
  saveLocal();
  updateHeader();
  renderChatList();
  renderMessages();
  if (state.firebaseReady) await listenTarget(target);
}

async function sendMessage(text) {
  const body = String(text || '').trim();
  if (!body) return;

  if (body.startsWith('/')) {
    await runCommand(body);
    return;
  }

  if (!state.db) {
    addSystemMessage('Firebase is not connected. Message not sent.');
    return;
  }

  const payload = {
    v: 2,
    id: crypto.randomUUID(),
    type: 'msg',
    fromId: state.userId,
    from_id: state.userId,
    fromName: state.username,
    from_name: state.username,
    to: outgoingToForTarget(state.activeTarget),
    channel: state.activeTarget.startsWith('@') ? state.activeTarget : null,
    text: body,
    body,
    ts: nowSec(),
    enc: 'none',
    scope: state.activeTarget.startsWith('@') ? 'public' : 'private',
    source: 'web-chat',
    status: 'sending',
  };

  addMessage(state.activeTarget, payload);
  try {
    await push(ref(state.db, state.currentPath), payload);
    addMessage(state.activeTarget, { ...payload, status: 'sent' });
  } catch (e) {
    addSystemMessage(`Send failed: ${e?.message || e}`);
  }
}

function findUserMatches(raw) {
  const q = String(raw || '').trim().toLowerCase();
  if (!q) return [];
  return state.userDirectory
    .filter((u) => String(u.username || '').toLowerCase().includes(q))
    .slice(0, 150);
}

function renderPeerSearchResults(query) {
  if (!el.peerSearchResults) return [];
  el.peerSearchResults.innerHTML = '';
  const q = String(query || '').trim();
  if (!q) {
    const li = document.createElement('li');
    li.textContent = 'Type to search usernames.';
    el.peerSearchResults.appendChild(li);
    return [];
  }
  const matches = findUserMatches(q);
  if (!matches.length) {
    const li = document.createElement('li');
    li.textContent = 'No match found.';
    el.peerSearchResults.appendChild(li);
    return matches;
  }
  for (const u of matches) {
    const li = document.createElement('li');
    li.textContent = `${u.username}${u.peerId ? ` (${u.peerId})` : ''}`;
    li.onclick = () => {
      if (u.peerId) switchTarget(u.peerId);
    };
    el.peerSearchResults.appendChild(li);
  }
  return matches;
}

function runPeerSearch(raw) {
  const q = String(raw || '').trim();
  const matches = renderPeerSearchResults(q);
  if (!q) {
    addSystemMessage('Usage: /peer-search <text>');
    return;
  }
  if (!matches.length) {
    addSystemMessage(`No usernames contain "${q}".`);
    return;
  }
  addSystemMessage(`Matches for "${q}": ${matches.slice(0, 30).map((m) => m.username).join(', ')}`);
}

function printHelp() {
  addSystemMessage('Commands: /help, /join <@channel>, /connect <peer_id>, /peer-search <text>, /theme [light|dark|toggle], /status, /load [n]');
}

async function runCommand(raw) {
  const parts = String(raw || '').trim().split(/\s+/);
  const cmd = (parts[0] || '').toLowerCase();

  if (cmd === '/help') return printHelp();
  if (cmd === '/status') {
    addSystemMessage(`target=${state.activeTarget} connected=${state.connected} users=${state.userDirectory.length} peers=${state.peers.length}`);
    return;
  }
  if (cmd === '/join') {
    const t = parts[1];
    if (!t) return addSystemMessage('Usage: /join <@channel>');
    return switchTarget(t.startsWith('@') ? t : `@${t}`);
  }
  if (cmd === '/connect') {
    const t = parts[1];
    if (!t) return addSystemMessage('Usage: /connect <peer_id>');
    return switchTarget(t);
  }
  if (cmd === '/peer-search') {
    return runPeerSearch(raw.replace(/^\/peer-search\s*/i, ''));
  }
  if (cmd === '/theme') {
    const next = (parts[1] || 'toggle').toLowerCase();
    if (next === 'toggle') toggleTheme();
    else if (next === 'light' || next === 'dark') applyTheme(next);
    else return addSystemMessage('Usage: /theme [light|dark|toggle]');
    addSystemMessage(`Theme set to ${state.theme}.`);
    return;
  }
  if (cmd === '/load') {
    const n = Number(parts[1] || state.autoLoadLimit);
    const lim = Number.isFinite(n) && n > 0 ? Math.min(500, Math.floor(n)) : state.autoLoadLimit;
    await loadLatest(lim);
    return;
  }

  addSystemMessage(`Unknown command: ${cmd}`);
}

async function setTyping(active) {
  if (!state.db || !state.userId || !state.activeTarget) return;
  const path = typingPathForTarget(state.activeTarget);
  const myRef = ref(state.db, `${path}/${safeKey(state.userId)}`);
  if (active) {
    await set(myRef, { id: state.userId, name: state.username || 'Anonymous', ts: nowSec() });
    onDisconnect(myRef).remove();
  } else {
    await set(myRef, null);
  }
}

async function bootstrapFirebase() {
  if (window.HERMES_FIREBASE_CONFIG) state.cfg = window.HERMES_FIREBASE_CONFIG;
  else {
    const res = await fetch('/web-config');
    state.cfg = await res.json();
  }

  const fcfg = state.cfg.firebase_web || {};
  if (!fcfg.apiKey || !fcfg.databaseURL || !fcfg.projectId) {
    throw new Error('Firebase config is incomplete.');
  }

  state.app = initializeApp(fcfg);
  state.db = getDatabase(state.app);
  state.auth = getAuth(state.app);
  state.firebaseReady = true;

  if (!state.userId) {
    const cred = await signInAnonymously(state.auth);
    state.userId = cred.user.uid;
  }
  if (!state.username) state.username = 'Anonymous';
  saveLocal();

  state.unsubs.connected = onValue(ref(state.db, '.info/connected'), async (s) => {
    state.connected = !!s.val();
    if (state.connected) {
      try { await setPresence(); } catch {}
    }
    updateHeader();
  });

  state.unsubs.presence = onValue(ref(state.db, 'presence'), (s) => {
    const val = s.val() || {};
    const peers = Object.values(val)
      .filter((p) => p && p.id && p.id !== state.userId && p.online)
      .map((p) => ({ id: String(p.id), name: String(p.name || p.id), ip: p.ip, port: p.port }))
      .sort((a, b) => a.name.localeCompare(b.name));
    state.peers = peers;
    state.peerById = Object.fromEntries(peers.map((p) => [p.id, p]));
    renderChatList();
    updateHeader();
  });

  state.unsubs.users = onValue(ref(state.db, 'users'), (s) => {
    const raw = s.val() || {};
    const users = [];
    for (const [username, data] of Object.entries(raw)) {
      if (!username) continue;
      users.push({
        username: String(username),
        peerId: data && (data.peer_id || data.peerId) ? String(data.peer_id || data.peerId) : '',
      });
    }
    users.sort((a, b) => a.username.localeCompare(b.username));
    state.userDirectory = users;
    state.usernameById = {};
    for (const u of users) {
      if (u.peerId) state.usernameById[u.peerId] = u.username;
    }
    updateHeader();
    renderChatList();
    renderMessages();
    if (el.peerSearchInput && el.peerSearchInput.value.trim()) {
      renderPeerSearchResults(el.peerSearchInput.value);
    }
  });

  if (state.unsubs.inbox) state.unsubs.inbox();
  const inboxPath = `messages/${safeKey(state.userId)}`;
  state.unsubs.inbox = onChildAdded(query(ref(state.db, inboxPath), orderByChild('ts'), limitToLast(200)), (snap) => {
    const msg = snap.val();
    if (!msg || typeof msg !== 'object') return;
    const m = normalizeIncoming(msg);
    const target = m.fromId === state.userId ? m.to : m.fromId;
    if (!target) return;
    addMessage(target, m);
  });
}

function wireUi() {
  el.messageInput.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = el.messageInput.value;
    el.messageInput.value = '';
    if (state.typingIdleTimer) clearTimeout(state.typingIdleTimer);
    setTyping(false).catch(() => {});
    await sendMessage(v);
  });

  el.messageInput.addEventListener('input', () => {
    setTyping(true).catch(() => {});
    if (state.typingIdleTimer) clearTimeout(state.typingIdleTimer);
    state.typingIdleTimer = setTimeout(() => {
      setTyping(false).catch(() => {});
    }, 1500);
  });

  el.sendBtn.onclick = async () => {
    const v = el.messageInput.value;
    el.messageInput.value = '';
    await sendMessage(v);
  };

  if (el.chatFilter) {
    el.chatFilter.addEventListener('input', () => {
      state.chatFilter = el.chatFilter.value || '';
      renderChatList();
    });
  }

  if (el.peerSearchBtn && el.peerSearchInput) {
    el.peerSearchBtn.onclick = () => runPeerSearch(el.peerSearchInput.value);
    el.peerSearchInput.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      runPeerSearch(el.peerSearchInput.value);
    });
  }

  el.btnTheme.onclick = () => toggleTheme();
  el.btnBroadcast.onclick = () => switchTarget('@broadcast');
  el.btnMenu.onclick = () => {
    const isMobile = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
    if (isMobile) {
      el.appRoot.classList.toggle('mobile');
      return;
    }
    setSidebarHidden(!state.sidebarHidden);
  };
  el.btnNewChat.onclick = () => { el.settingsOverlay.classList.add('open'); };
  el.btnSettings.onclick = () => { el.settingsOverlay.classList.add('open'); };
  el.settingsClose.onclick = () => { el.settingsOverlay.classList.remove('open'); };

  el.settingsName.value = state.username;
  el.settingsTarget.value = state.activeTarget;

  el.settingsSave.onclick = async () => {
    const next = el.settingsName.value.trim();
    if (next) {
      state.username = next;
      saveLocal();
      updateHeader();
      if (state.firebaseReady) await setPresence();
    }
    const target = normalizeTarget(el.settingsTarget.value || '@broadcast');
    await switchTarget(target);
    el.settingsOverlay.classList.remove('open');
    el.messageInput.focus();
  };
}

async function start() {
  applyTheme(state.theme);
  setSidebarHidden(state.sidebarHidden);
  wireUi();
  ensureChat(state.activeTarget);
  renderChatList();
  renderMessages();
  updateHeader();
  if (el.peerSearchResults) renderPeerSearchResults('');

  try {
    await bootstrapFirebase();
  } catch (err) {
    addSystemMessage(`Firebase init failed: ${err?.message || err}`);
  }

  await switchTarget(state.activeTarget || '@broadcast');
  el.settingsOverlay.classList.remove('open');
}

start().catch((err) => {
  console.error(err);
  addSystemMessage(`Startup error: ${err?.message || err}`);
});
