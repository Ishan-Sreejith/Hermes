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
};

const el = {
  statusText: document.getElementById('connection-text'),
  transportInfo: document.getElementById('transport-info'),
  chatMessages: document.getElementById('chat-messages'),
  messageInput: document.getElementById('message-input'),
  chatTitle: document.getElementById('chat-title'),
  chatList: document.getElementById('chat-list'),
  btnCreate: document.getElementById('btn-create'),
  btnRename: document.getElementById('btn-rename'),
  btnDelete: document.getElementById('btn-delete'),
  btnTheme: document.getElementById('btn-theme'),
  settingsOverlay: document.getElementById('settings-overlay'),
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
  sidebar: document.querySelector('.sidebar'),
};

let modalMode = 'create';

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

function normalizeChannel(name) {
  const raw = String(name || '').trim();
  if (!raw) return '';
  return raw.startsWith('@') ? raw.toLowerCase() : `@${raw.toLowerCase()}`;
}

function channelPath(channel) {
  const c = normalizeChannel(channel);
  if (c === '@broadcast') return 'messages/broadcast';
  return `messages/chan_${c.slice(1)}`;
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
  el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
}

function updateChatList(channels) {
  el.chatList.innerHTML = '';
  channels.forEach((channel) => {
    const last = (state.chatCache[channel] || []).slice(-1)[0];
    const card = document.createElement('div');
    card.className = `chat-card ${channel === state.activeTarget ? 'active' : ''}`;
    const unreadCount = Number(state.unread[channel] || 0);
    const unreadBadge = unreadCount > 0 ? `<span class="badge">${unreadCount}</span>` : '';
    card.innerHTML = `<div><strong>${escapeHtml(channel)}</strong>${unreadBadge}</div><div class="meta">${escapeHtml(last ? String(last.body || '').slice(0, 60) : 'No messages yet')}</div>`;
    card.addEventListener('click', () => switchTarget(channel));
    el.chatList.appendChild(card);
  });
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
  const key = c.slice(1);
  await set(ref(state.db, `rooms/${key}`), { created_at: Date.now(), created_by: state.userId });
  switchTarget(c);
}

async function deleteChannel(channel) {
  const c = normalizeChannel(channel);
  if (!c || c === '@broadcast') return;
  const key = c.slice(1);
  await remove(ref(state.db, `rooms/${key}`));
  await remove(ref(state.db, `messages/chan_${key}`));
  if (state.activeTarget === c) switchTarget('@broadcast');
}

async function renameChannel(oldChannel, newChannel) {
  const oldC = normalizeChannel(oldChannel);
  const newC = normalizeChannel(newChannel);
  if (!oldC || !newC || oldC === '@broadcast' || newC === '@broadcast') return;
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
  el.btnRename.addEventListener('click', () => openModal('rename'));
  el.btnDelete.addEventListener('click', () => openModal('delete'));

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
  if (el.sidebarToggle) {
    el.sidebarToggle.addEventListener('click', () => {
      el.sidebar.classList.toggle('open');
    });
  }
  if (el.sidebar) {
    el.sidebar.addEventListener('click', (event) => {
      const card = event.target.closest('.chat-card');
      if (card) el.sidebar.classList.remove('open');
    });
  }

  el.settingsSave.addEventListener('click', () => {
    state.username = String(el.settingsName.value || '').trim();
    localStorage.setItem('hermesUsername', state.username);
    el.settingsOverlay.classList.remove('open');
    bootstrap();
  });
  if (el.settingsCancel) {
    el.settingsCancel.addEventListener('click', () => {
      state.username = state.username || 'Anon';
      localStorage.setItem('hermesUsername', state.username);
      el.settingsOverlay.classList.remove('open');
      bootstrap();
    });
  }
}

wireEvents();
updateConnectionUi();
if (state.username) {
  bootstrap();
} else {
  el.settingsOverlay.classList.add('open');
}
