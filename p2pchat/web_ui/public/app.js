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
  chatModal: document.getElementById('chat-modal'),
  chatModalTitle: document.getElementById('chat-modal-title'),
  chatModalName: document.getElementById('chat-modal-name'),
  chatModalNewWrap: document.getElementById('chat-modal-new-wrap'),
  chatModalNewName: document.getElementById('chat-modal-new-name'),
  chatModalCancel: document.getElementById('chat-modal-cancel'),
  chatModalConfirm: document.getElementById('chat-modal-confirm'),
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

async function bootstrap() {
  if (!window.HERMES_FIREBASE_CONFIG) return;
  const app = initializeApp(window.HERMES_FIREBASE_CONFIG.firebase_web);
  state.db = getDatabase(app);
  const cred = await signInAnonymously(getAuth(app));
  state.userId = cred.user.uid;

  onValue(ref(state.db, '.info/connected'), (snap) => {
    const ok = !!snap.val();
    if (el.statusText) el.statusText.textContent = ok ? 'Cloud Online' : 'Offline';
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
      const time = m.ts ? new Date(m.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
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
    card.innerHTML = `<div><strong>${escapeHtml(channel)}</strong></div><div class="meta">${escapeHtml(last ? String(last.body || '').slice(0, 60) : 'No messages')}</div>`;
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
      if (key === state.activeTarget) renderMessages();
    }
  });
}

function switchTarget(target) {
  const normalized = normalizeChannel(target);
  if (!normalized) return;
  state.activeTarget = normalized;
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
  el.messageInput.value = '';
  const msg = {
    id: crypto.randomUUID(),
    body,
    fromId: state.userId,
    fromName: state.username || 'Anon',
    ts: Date.now(),
    enc: 'none',
  };
  await push(ref(state.db, channelPath(state.activeTarget)), msg);
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

  el.settingsSave.addEventListener('click', () => {
    state.username = String(el.settingsName.value || '').trim();
    localStorage.setItem('hermesUsername', state.username);
    el.settingsOverlay.classList.remove('open');
    bootstrap();
  });
}

wireEvents();
if (state.username) bootstrap();
else el.settingsOverlay.classList.add('open');
