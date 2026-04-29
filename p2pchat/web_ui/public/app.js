import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.13.2/firebase-app.js';
import {
  getDatabase,
  ref,
  push,
  set,
  get,
  remove,
  onChildAdded,
  onValue,
  query,
  limitToLast,
} from 'https://www.gstatic.com/firebasejs/10.13.2/firebase-database.js';
import { getAuth, signInAnonymously } from 'https://www.gstatic.com/firebasejs/10.13.2/firebase-auth.js';

const state = {
  db: null,
  userId: null,
  username: localStorage.getItem('hermesUsername') || '',
  activeTarget: localStorage.getItem('hermesActiveTarget') || '@broadcast',
  chatCache: {},
  listeners: new Set(),
  pending: new Map(),
  bootstrapped: false,
  connected: false,
  unread: {},
  directTargets: (() => {
    try {
      const parsed = JSON.parse(localStorage.getItem('hermesDirectTargets') || '[]');
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  })(),
};
const AUTH_CACHE_KEY = 'hermesAuthV1';

const el = {
  statusText: document.getElementById('connection-text'),
  transportInfo: document.getElementById('transport-info'),
  chatMessages: document.getElementById('chat-messages'),
  messageInput: document.getElementById('message-input'),
  chatTitle: document.getElementById('chat-title'),
  chatList: document.getElementById('chat-list'),
  btnCreate: document.getElementById('btn-create'),
  btnTheme: document.getElementById('btn-theme'),
  btnSettings: document.getElementById('btn-settings'),
  settingsOverlay: document.getElementById('settings-overlay'),
  settingsTitle: document.getElementById('settings-title'),
  settingsName: document.getElementById('settings-name'),
  settingsSave: document.getElementById('settings-save'),
  settingsCancel: document.getElementById('settings-cancel'),
  chatModal: document.getElementById('chat-modal'),
  chatModalTitle: document.getElementById('chat-modal-title'),
  chatModalName: document.getElementById('chat-modal-name'),
  chatModalNewWrap: document.getElementById('chat-modal-new-wrap'),
  chatModalNewName: document.getElementById('chat-modal-new-name'),
  chatModalCancel: document.getElementById('chat-modal-cancel'),
  chatModalConfirm: document.getElementById('chat-modal-confirm'),
  sendBtn: document.getElementById('send-btn'),
  errorBanner: document.getElementById('error-banner'),
  sidebarToggle: document.getElementById('sidebar-toggle'),
  sidebarCollapse: document.getElementById('sidebar-collapse'),
  sidebar: document.querySelector('.sidebar'),
  appShell: document.getElementById('app-shell'),
  authGate: document.getElementById('auth-gate'),
  authUsername: document.getElementById('auth-username'),
  authPassword: document.getElementById('auth-password'),
  authContinue: document.getElementById('auth-continue'),
  authError: document.getElementById('auth-error'),
  chatMenu: document.getElementById('chat-menu'),
  chatMenuRename: document.getElementById('chat-menu-rename'),
  chatMenuDelete: document.getElementById('chat-menu-delete'),
};

let modalMode = 'create';
let settingsMode = 'welcome';
const uiPrefs = {
  sidebarCollapsed: localStorage.getItem('hermesSidebarCollapsed') === '1',
};
let contextChannel = '';

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

function normalizeChannel(name) {
  const raw = String(name || '').trim();
  if (!raw) return '';
  if (raw.startsWith('#')) {
    return `#${raw.slice(1).trim()}`;
  }
  return raw.startsWith('@') ? raw.toLowerCase() : `@${raw.toLowerCase()}`;
}

function channelPath(channel) {
  const c = normalizeChannel(channel);
  if (c === '@broadcast') return 'messages/broadcast';
  if (c.startsWith('#')) return `messages/${c.slice(1)}`;
  return `messages/chan_${c.slice(1)}`;
}

async function resolveDirectTarget(raw) {
  const token = String(raw || '').trim().replace(/^#+/, '');
  if (!token) return '';
  if (token.startsWith('user_') || /^[0-9a-f-]{16,}$/i.test(token)) {
    return token;
  }
  try {
    const primary = await get(ref(state.db, `users/${token}`));
    let val = primary.val() || {};
    if (!val.peer_id && !val.peerId) {
      const lower = await get(ref(state.db, `users/${token.toLowerCase()}`));
      val = lower.val() || {};
    }
    const peer = val.peer_id || val.peerId || '';
    return String(peer || token);
  } catch {
    return token;
  }
}

function persistDirectTargets() {
  localStorage.setItem('hermesDirectTargets', JSON.stringify(state.directTargets.slice(0, 40)));
}

function setBanner(message) {
  if (!el.errorBanner) return;
  if (!message) {
    el.errorBanner.textContent = '';
    el.errorBanner.classList.remove('open');
    return;
  }
  el.errorBanner.textContent = message;
  el.errorBanner.classList.add('open');
}

function readAuthCache() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_CACHE_KEY) || 'null');
  } catch {
    return null;
  }
}

async function hashPassword(raw) {
  const bytes = new TextEncoder().encode(String(raw || ''));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

async function completeAuthGate() {
  const name = String(el.authUsername?.value || '').trim();
  const password = String(el.authPassword?.value || '');
  if (!name) {
    el.authError.textContent = 'Username is required.';
    return;
  }
  if (password.length < 4) {
    el.authError.textContent = 'Password must be at least 4 characters.';
    return;
  }
  const passHash = await hashPassword(password);
  localStorage.setItem(
    AUTH_CACHE_KEY,
    JSON.stringify({ username: name, passHash, createdAt: Date.now() }),
  );
  state.username = name;
  localStorage.setItem('hermesUsername', state.username);
  el.authGate.classList.remove('open');
  bootstrap();
}

function initAuthGate() {
  const cached = readAuthCache();
  if (cached && cached.username && cached.passHash) {
    state.username = cached.username;
    localStorage.setItem('hermesUsername', state.username);
    el.authGate.classList.remove('open');
    bootstrap();
    return;
  }
  el.authGate.classList.add('open');
  if (el.authUsername) el.authUsername.value = state.username || '';
  el.authError.textContent = '';
  el.authUsername?.focus();
}

function updateConnectionUi() {
  const connected = !!state.connected;
  if (el.statusText) {
    el.statusText.textContent = connected ? 'Cloud Online' : 'Offline';
    el.statusText.classList.toggle('good', connected);
    el.statusText.classList.toggle('bad', !connected);
  }
  const canSend = connected && state.bootstrapped;
  if (el.sendBtn) el.sendBtn.disabled = !canSend;
}

function applySidebarState() {
  if (!el.appShell) return;
  el.appShell.classList.toggle('sidebar-collapsed', !!uiPrefs.sidebarCollapsed);
}

async function resolveFirebaseWebConfig() {
  const fromWindow = window?.HERMES_FIREBASE_CONFIG?.firebase_web;
  if (fromWindow && typeof fromWindow === 'object') {
    return fromWindow;
  }
  try {
    const confRes = await fetch('/web-config');
    if (!confRes.ok) return null;
    const contentType = String(confRes.headers.get('content-type') || '').toLowerCase();
    if (!contentType.includes('application/json')) {
      return null;
    }
    const conf = await confRes.json();
    return conf?.firebase_web || null;
  } catch (err) {
    return null;
  }
}

async function bootstrap() {
  if (state.bootstrapped && state.db) return;
  if (state.bootstrapped && !state.db) {
    state.bootstrapped = false;
  }
  const firebaseWeb = (await resolveFirebaseWebConfig()) || {};
  if (!firebaseWeb.apiKey || String(firebaseWeb.apiKey).startsWith('YOUR_')) {
    setBanner('Firebase config is not set. Update firebase-config.js or FIREBASE_* env vars.');
    return;
  }
  const app = initializeApp(firebaseWeb);
  state.db = getDatabase(app);
  const auth = getAuth(app);
  let cred;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      cred = await signInAnonymously(auth);
      break;
    } catch (err) {
      await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
  if (!cred) {
    setBanner('Anonymous auth failed. Check Firebase Auth + RTDB setup.');
    state.bootstrapped = false;
    state.connected = false;
    updateConnectionUi();
    return;
  }
  state.userId = cred.user.uid;
  state.bootstrapped = true;
  setBanner('');

  onValue(ref(state.db, '.info/connected'), (snap) => {
    state.connected = !!snap.val();
    updateConnectionUi();
  });

  listenRooms();
  switchTarget(state.activeTarget);
}

function renderMessages() {
  const msgs = state.chatCache[state.activeTarget] || [];
  const nearBottom =
    el.chatMessages.scrollHeight - el.chatMessages.scrollTop - el.chatMessages.clientHeight <
    120;
  el.chatMessages.innerHTML = msgs
    .slice(-300)
    .map((m) => {
      const me = m.fromId === state.userId;
      const time = m.ts
        ? new Date(m.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : '';
      return `<div class="msg ${me ? 'me' : ''}"><div>${escapeHtml(m.body || '')}</div><div class="meta">${escapeHtml(me ? 'You' : m.fromName || 'Unknown')} ${escapeHtml(time)}</div></div>`;
    })
    .join('');
  if (nearBottom) {
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
  }
}

function updateChatList(channels) {
  el.chatList.innerHTML = '';
  channels.forEach((channel) => {
    const last = (state.chatCache[channel] || []).slice(-1)[0];
    const card = document.createElement('div');
    card.className = `chat-card ${channel === state.activeTarget ? 'active' : ''}`;
    card.dataset.channel = channel;
    const unreadCount = Number(state.unread[channel] || 0);
    const unreadBadge = unreadCount > 0 ? `<span class="badge">${unreadCount}</span>` : '';
    const showMenu = channel !== '@broadcast';
    card.innerHTML = `<div class="chat-card-row"><div><strong>${escapeHtml(channel)}</strong>${unreadBadge}</div>${showMenu ? `<button class="chat-menu-trigger" data-channel="${escapeHtml(channel)}" aria-label="Channel options" title="Channel options">&#8942;</button>` : ''}</div><div class="meta">${escapeHtml(last ? String(last.body || '').slice(0, 60) : 'No messages yet')}</div>`;
    card.addEventListener('click', () => {
      closeChatMenu();
      switchTarget(channel);
    });
    const trigger = card.querySelector('.chat-menu-trigger');
    if (trigger) {
      trigger.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        openChatMenu(channel, event.clientX, event.clientY);
      });
    }
    el.chatList.appendChild(card);
  });
}

function openChatMenu(channel, x, y) {
  if (!el.chatMenu) return;
  if (normalizeChannel(channel) === '@broadcast') return;
  contextChannel = normalizeChannel(channel);
  el.chatMenu.classList.add('open');
  const menuWidth = 160;
  const menuHeight = 90;
  const left = Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8));
  const top = Math.max(8, Math.min(y, window.innerHeight - menuHeight - 8));
  el.chatMenu.style.left = `${left}px`;
  el.chatMenu.style.top = `${top}px`;
}

function closeChatMenu() {
  if (!el.chatMenu) return;
  el.chatMenu.classList.remove('open');
  contextChannel = '';
}

function ensureChannelListener(channel) {
  const key = normalizeChannel(channel);
  if (!key || state.listeners.has(key)) return;
  state.listeners.add(key);
  onChildAdded(query(ref(state.db, channelPath(key)), limitToLast(200)), (snap) => {
    const msg = snap.val() || {};
    if (!state.chatCache[key]) state.chatCache[key] = [];
    if (!state.chatCache[key].some((m) => m.id === msg.id)) {
      state.chatCache[key].push(msg);
      state.chatCache[key].sort((a, b) => (a.ts || 0) - (b.ts || 0));
      if (key === state.activeTarget) {
        renderMessages();
      } else if (msg.fromId !== state.userId) {
        state.unread[key] = Number(state.unread[key] || 0) + 1;
        refreshRooms();
      }
    }
  });
}

function switchTarget(target) {
  const normalized = normalizeChannel(target);
  if (!normalized) return;
  state.activeTarget = normalized;
  state.unread[normalized] = 0;
  localStorage.setItem('hermesActiveTarget', normalized);
  el.chatTitle.textContent = normalized;
  el.transportInfo.textContent = 'Cloud Relay';
  ensureChannelListener(normalized);
  renderMessages();
  refreshRooms();
  if (window.innerWidth <= 860) el.sidebar.classList.remove('open');
}

async function refreshRooms() {
  const rooms = await get(ref(state.db, 'rooms'));
  const channels = ['@broadcast'];
  const val = rooms.val() || {};
  Object.keys(val)
    .sort()
    .forEach((k) => {
      const c = `@${String(k).toLowerCase()}`;
      channels.push(c);
      ensureChannelListener(c);
    });
  for (const d of state.directTargets) {
    if (d && !channels.includes(d)) {
      channels.push(d);
      ensureChannelListener(d);
    }
  }
  if (!channels.includes(state.activeTarget)) {
    state.activeTarget = '@broadcast';
  }
  updateChatList(channels);
}

function listenRooms() {
  onValue(ref(state.db, 'rooms'), () => {
    refreshRooms();
  });
}

async function send() {
  const body = String(el.messageInput.value || '').trim();
  if (!body) return;
  if (body.startsWith('/me ')) {
    const action = body.slice(4).trim();
    if (action) {
      const name = state.username || 'Anon';
      el.messageInput.value = '';
      return sendText(`* ${name} ${action}`);
    }
  }
  el.messageInput.value = '';
  await sendText(body);
}

async function sendText(body, overrideMsg) {
  if (!state.bootstrapped || !state.db || !state.connected) {
    setBanner('Not connected. Message not sent.');
    return;
  }
  const msg = overrideMsg || {
    id: crypto.randomUUID(),
    body,
    fromId: state.userId,
    fromName: state.username || 'Anon',
    ts: Date.now(),
    enc: 'none',
  };
  if (!state.chatCache[state.activeTarget]) state.chatCache[state.activeTarget] = [];
  state.chatCache[state.activeTarget].push(msg);
  state.chatCache[state.activeTarget].sort((a, b) => (a.ts || 0) - (b.ts || 0));
  renderMessages();
  state.pending.set(msg.id, msg);
  try {
    await push(ref(state.db, channelPath(state.activeTarget)), msg);
    state.pending.delete(msg.id);
    setBanner('');
  } catch (err) {
    state.pending.delete(msg.id);
    setBanner('Send failed. Check connectivity and database rules.');
  }
}

function openModal(mode) {
  modalMode = mode;
  el.chatModalTitle.textContent =
    mode === 'create' ? 'Create Channel' : mode === 'rename' ? 'Rename Channel' : 'Delete Channel';
  el.chatModalName.value = mode === 'rename' || mode === 'delete' ? state.activeTarget : '';
  el.chatModalNewWrap.style.display = mode === 'rename' ? 'block' : 'none';
  el.chatModalNewName.value = '';
  el.chatModal.classList.add('open');
}

function closeModal() {
  el.chatModal.classList.remove('open');
}

async function createChannel(channel) {
  const c = normalizeChannel(channel);
  if (!c || c === '@broadcast') return;
  if (c.startsWith('#')) {
    const resolved = await resolveDirectTarget(c);
    if (!resolved) return;
    const target = `#${resolved}`;
    if (!state.directTargets.includes(target)) {
      state.directTargets.push(target);
      persistDirectTargets();
    }
    switchTarget(target);
    await refreshRooms();
    return;
  }
  const key = c.slice(1);
  await set(ref(state.db, `rooms/${key}`), { created_at: Date.now(), created_by: state.userId });
  switchTarget(c);
}

async function deleteChannel(channel) {
  const c = normalizeChannel(channel);
  if (!c || c === '@broadcast') return;
  if (c.startsWith('#')) {
    state.directTargets = state.directTargets.filter((x) => x !== c);
    persistDirectTargets();
    state.chatCache[c] = [];
    delete state.unread[c];
    if (state.activeTarget === c) switchTarget('@broadcast');
    await refreshRooms();
    return;
  }
  const ok = window.confirm(`Delete ${c}? This cannot be undone.`);
  if (!ok) return;
  const key = c.slice(1);
  await Promise.all([
    remove(ref(state.db, `rooms/${key}`)),
    remove(ref(state.db, `messages/chan_${key}`)),
  ]);
  state.chatCache[c] = [];
  delete state.unread[c];
  if (state.activeTarget === c) switchTarget('@broadcast');
  await refreshRooms();
}

async function renameChannel(oldChannel, newChannel) {
  const oldC = normalizeChannel(oldChannel);
  const newC = normalizeChannel(newChannel);
  if (!oldC || !newC || oldC === '@broadcast' || newC === '@broadcast') return;
  if (oldC.startsWith('#')) {
    const resolved = newC.startsWith('#') ? await resolveDirectTarget(newC) : '';
    if (!resolved) return;
    const next = `#${resolved}`;
    state.directTargets = state.directTargets.filter((x) => x !== oldC);
    if (!state.directTargets.includes(next)) state.directTargets.push(next);
    persistDirectTargets();
    if (state.activeTarget === oldC) switchTarget(next);
    await refreshRooms();
    return;
  }
  if (oldC === newC) return;
  const oldKey = oldC.slice(1);
  const newKey = newC.slice(1);
  const [roomSnap, msgSnap] = await Promise.all([
    get(ref(state.db, `rooms/${oldKey}`)),
    get(ref(state.db, `messages/chan_${oldKey}`)),
  ]);
  const roomVal = roomSnap.val() || { created_at: Date.now(), created_by: state.userId };
  await set(ref(state.db, `rooms/${newKey}`), roomVal);
  if (msgSnap.exists()) {
    await set(ref(state.db, `messages/chan_${newKey}`), msgSnap.val());
  }
  await remove(ref(state.db, `rooms/${oldKey}`));
  await remove(ref(state.db, `messages/chan_${oldKey}`));
  if (state.activeTarget === oldC) switchTarget(newC);
  refreshRooms();
}

function wireEvents() {
  document.getElementById('send-btn').addEventListener('click', send);
  el.messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') send();
  });

  el.btnCreate.addEventListener('click', () => openModal('create'));

  el.chatModalCancel.addEventListener('click', closeModal);
  el.chatModalConfirm.addEventListener('click', async () => {
    const a = el.chatModalName.value;
    const b = el.chatModalNewName.value;
    if (modalMode === 'create') await createChannel(a);
    if (modalMode === 'rename') await renameChannel(a, b);
    if (modalMode === 'delete') await deleteChannel(a);
    closeModal();
    refreshRooms();
  });

  el.btnTheme.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', current === 'dark' ? 'light' : 'dark');
  });
  if (el.btnSettings) {
    el.btnSettings.addEventListener('click', () => {
      settingsMode = 'edit';
      if (el.settingsTitle) el.settingsTitle.textContent = 'Settings';
      if (el.settingsCancel) el.settingsCancel.textContent = 'Close';
      if (el.settingsSave) el.settingsSave.textContent = 'Save';
      if (el.settingsName) el.settingsName.value = state.username || '';
      el.settingsOverlay.classList.add('open');
      el.settingsName?.focus();
    });
  }
  if (el.sidebarToggle) {
    el.sidebarToggle.addEventListener('click', () => {
      if (window.innerWidth <= 860) {
        el.sidebar.classList.toggle('open');
      } else {
        uiPrefs.sidebarCollapsed = !uiPrefs.sidebarCollapsed;
        localStorage.setItem('hermesSidebarCollapsed', uiPrefs.sidebarCollapsed ? '1' : '0');
        applySidebarState();
      }
    });
  }
  if (el.sidebarCollapse) {
    el.sidebarCollapse.addEventListener('click', () => {
      uiPrefs.sidebarCollapsed = true;
      localStorage.setItem('hermesSidebarCollapsed', '1');
      applySidebarState();
    });
  }
  if (el.sidebar) {
    el.sidebar.addEventListener('click', (event) => {
      const card = event.target.closest('.chat-card');
      if (card) el.sidebar.classList.remove('open');
    });
  }
  if (el.chatMenuRename) {
    el.chatMenuRename.addEventListener('click', () => {
      if (!contextChannel) return;
      closeChatMenu();
      openModal('rename');
      el.chatModalName.value = contextChannel;
    });
  }
  if (el.chatMenuDelete) {
    el.chatMenuDelete.addEventListener('click', async () => {
      if (!contextChannel) return;
      const target = contextChannel;
      closeChatMenu();
      await deleteChannel(target);
      await refreshRooms();
    });
  }
  window.addEventListener('click', (event) => {
    if (!el.chatMenu?.classList.contains('open')) return;
    if (!event.target.closest('#chat-menu')) {
      closeChatMenu();
    }
  });

  el.settingsSave.addEventListener('click', () => {
    const nextName = String(el.settingsName.value || '').trim();
    state.username = nextName || 'Anon';
    localStorage.setItem('hermesUsername', state.username);
    el.settingsOverlay.classList.remove('open');
    bootstrap();
  });
  if (el.settingsCancel) {
    el.settingsCancel.addEventListener('click', () => {
      el.settingsOverlay.classList.remove('open');
      if (settingsMode === 'welcome') {
        state.username = state.username || 'Anon';
        localStorage.setItem('hermesUsername', state.username);
        bootstrap();
      }
    });
  }
}

wireEvents();
applySidebarState();
updateConnectionUi();
if (el.authContinue) {
  el.authContinue.addEventListener('click', () => {
    completeAuthGate();
  });
}
if (el.authPassword) {
  el.authPassword.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') completeAuthGate();
  });
}
if (el.authUsername) {
  el.authUsername.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') completeAuthGate();
  });
}
initAuthGate();
