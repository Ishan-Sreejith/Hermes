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

const MAX_LINES = 1800;

const state = {
  cfg: null,
  app: null,
  db: null,
  auth: null,
  userId: localStorage.getItem('hermesUserId') || null,
  username: localStorage.getItem('hermesUsername') || '',
  activeTarget: localStorage.getItem('hermesActiveTarget') || '@broadcast',
  encMode: localStorage.getItem('hermesEncMode') || 'none',
  autoLoad: true,
  autoLoadLimit: 100,
  connected: false,
  peers: [],
  chats: new Set(['@broadcast']),
  currentPath: '',
  unsubs: { messages: null, inbox: null, presence: null, connected: null },
  seenKeys: new Set(),
};

const el = {
  output: document.getElementById('output'),
  cmd: document.getElementById('cmd'),
  prompt: document.getElementById('prompt-label'),
  statusLeft: document.getElementById('status-left'),
  statusRight: document.getElementById('status-right'),
  chatList: document.getElementById('chat-list'),
  peerList: document.getElementById('peer-list'),
  sessionCard: document.getElementById('session-card'),
  menuOverlay: document.getElementById('menu-overlay'),
  menuName: document.getElementById('menu-name'),
  menuSaveName: document.getElementById('menu-save-name'),
  menuOpenTarget: document.getElementById('menu-open-target'),
  menuOpen: document.getElementById('menu-open'),
  menuJoin: document.getElementById('menu-join'),
  menuJoinBtn: document.getElementById('menu-join-btn'),
  menuConnect: document.getElementById('menu-connect'),
  menuConnectBtn: document.getElementById('menu-connect-btn'),
};

function nowSec() {
  return Date.now() / 1000;
}

function saveLocal() {
  localStorage.setItem('hermesActiveTarget', state.activeTarget);
  localStorage.setItem('hermesEncMode', state.encMode);
  localStorage.setItem('hermesUsername', state.username);
  if (state.userId) localStorage.setItem('hermesUserId', state.userId);
}

function safeKey(value) {
  return String(value || '').replace(/[.#$/\[\]]/g, '_');
}

function fmtTs(tsSec) {
  const d = new Date(Number(tsSec || nowSec()) * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function toSeconds(tsRaw) {
  const n = Number(tsRaw || 0);
  if (!Number.isFinite(n) || n <= 0) return nowSec();
  // Support ms and sec timestamps.
  return n > 10_000_000_000 ? n / 1000 : n;
}

function line(text, cls = 'sys') {
  const row = document.createElement('div');
  row.className = `line ${cls}`;
  row.textContent = text;
  el.output.appendChild(row);
  while (el.output.children.length > MAX_LINES) {
    el.output.removeChild(el.output.firstChild);
  }
  el.output.scrollTop = el.output.scrollHeight;
}

function messageLine(msg) {
  const fromId = msg.fromId || msg.from_id || 'unknown';
  const fromName = msg.fromName || msg.from_name || fromId;
  const ts = fmtTs(toSeconds(msg.ts));
  const body = msg.text || msg.body || '';
  const me = fromId === state.userId;
  line(`[${ts}] ${fromName}${me ? ' (you)' : ''}: ${body}`, me ? 'me' : 'msg');
}

function updatePrompt() {
  el.prompt.textContent = `${state.activeTarget} ›`;
}

function updateStatus() {
  const mode = state.connected ? 'connected' : 'offline';
  el.statusLeft.textContent = `${state.username || 'anon'} (${state.userId ? state.userId.slice(0, 8) : 'no-id'}) | ${state.activeTarget} | ${mode}`;
  el.statusRight.textContent = `firebase-only | enc=${state.encMode} | auto-load=${state.autoLoad ? 'on' : 'off'}:${state.autoLoadLimit}`;
}

function updateSessionCard() {
  el.sessionCard.innerHTML = [
    `user: ${state.username || 'unset'}`,
    `id: ${state.userId || 'none'}`,
    `active: ${state.activeTarget}`,
    `enc: ${state.encMode}`,
    `transport: firebase`,
  ].join('<br/>');
}

function renderChats() {
  el.chatList.innerHTML = '';
  const items = Array.from(state.chats);
  items.sort((a, b) => a.localeCompare(b));
  for (const target of items) {
    const li = document.createElement('li');
    li.textContent = target;
    li.onclick = () => switchTarget(target);
    el.chatList.appendChild(li);
  }
}

function renderPeers() {
  el.peerList.innerHTML = '';
  for (const p of state.peers) {
    const li = document.createElement('li');
    li.textContent = `${p.name || p.id} (${p.id})`;
    li.onclick = () => runCommand(`/connect ${p.id}`);
    el.peerList.appendChild(li);
  }
}

function messagePathForTarget(target) {
  if (target === '@broadcast') return 'messages/broadcast';
  if (target.startsWith('@')) return `messages/chan_${safeKey(target.slice(1).toLowerCase())}`;
  return `messages/${safeKey(target)}`;
}

function outgoingToForTarget(target) {
  if (target === '@broadcast') return '@broadcast';
  return target;
}

async function listenTarget(target) {
  if (state.unsubs.messages) state.unsubs.messages();
  state.seenKeys.clear();
  state.currentPath = messagePathForTarget(target);

  const q = query(ref(state.db, state.currentPath), orderByChild('ts'), limitToLast(state.autoLoadLimit));
  state.unsubs.messages = onChildAdded(q, (snap) => {
    if (!state.autoLoad) return;
    const dedupe = `${state.currentPath}:${snap.key}`;
    if (state.seenKeys.has(dedupe)) return;
    state.seenKeys.add(dedupe);
    const msg = snap.val();
    if (!msg || typeof msg !== 'object') return;
    messageLine(msg);
  });
}

async function loadLatest(limit) {
  const q = query(ref(state.db, state.currentPath), orderByChild('ts'), limitToLast(limit));
  const s = await get(q);
  if (!s.exists()) {
    line('No messages found for current target.', 'warn');
    return;
  }
  const values = Object.values(s.val() || {});
  for (const msg of values) messageLine(msg);
}

async function sendChatMessage(text) {
  if (!text) return;
  const payload = {
    v: 2,
    id: crypto.randomUUID(),
    type: state.activeTarget.startsWith('@') ? 'msg' : 'msg',
    fromId: state.userId,
    from_id: state.userId,
    fromName: state.username,
    from_name: state.username,
    to: outgoingToForTarget(state.activeTarget),
    channel: state.activeTarget.startsWith('@') ? state.activeTarget : null,
    text,
    body: text,
    ts: nowSec(),
    enc: state.encMode,
    scope: state.activeTarget.startsWith('@') ? 'public' : 'private',
    source: 'web-terminal',
  };

  await push(ref(state.db, state.currentPath), payload);
  line(`queued (${state.activeTarget})`, 'ok');
}

async function setPresence() {
  if (!state.userId || !state.connected) return;
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
  if (!target) return;
  state.activeTarget = target;
  state.chats.add(target);
  saveLocal();
  updatePrompt();
  updateStatus();
  updateSessionCard();
  renderChats();
  line(`Switched to ${target}`, 'ok');
  await listenTarget(target);
}

function printHelp() {
  line('Commands:', 'sys');
  line('/help                              Show commands', 'sys');
  line('/join <@channel>                    Join/switch channel', 'sys');
  line('/connect <peer_id>                  Open direct chat target', 'sys');
  line('/load [n|on|off]                    Manual load or auto-load control', 'sys');
  line('/status                             Show session status', 'sys');
  line('/whoami                             Show current identity', 'sys');
  line('/enc <none|fernet|rsa|custom:...>   Set encryption tag', 'sys');
  line('/name <display_name>                Set display name', 'sys');
  line('/clear                              Clear terminal output', 'sys');
  line('/menu                               Reopen start menu', 'sys');
  line('/listen <port>                      Not supported in web (firebase-only)', 'sys');
}

async function runCommand(raw) {
  const parts = raw.trim().split(/\s+/);
  const cmd = (parts[0] || '').toLowerCase();

  if (cmd === '/help') return printHelp();
  if (cmd === '/clear') {
    el.output.innerHTML = '';
    return;
  }
  if (cmd === '/menu') {
    el.menuOverlay.style.display = 'flex';
    return;
  }
  if (cmd === '/status') {
    line(`transport=firebase connected=${state.connected} target=${state.activeTarget} peers=${state.peers.length} autoLoad=${state.autoLoad}:${state.autoLoadLimit}`, 'sys');
    return;
  }
  if (cmd === '/whoami') {
    line(`username=${state.username} peer_id=${state.userId}`, 'sys');
    return;
  }
  if (cmd === '/name') {
    const next = raw.replace(/^\/name\s+/, '').trim();
    if (!next) return line('Usage: /name <display_name>', 'warn');
    state.username = next;
    saveLocal();
    updateStatus();
    updateSessionCard();
    await setPresence();
    line(`Name updated to ${state.username}`, 'ok');
    return;
  }
  if (cmd === '/enc') {
    const next = raw.replace(/^\/enc\s+/, '').trim();
    if (!next) return line('Usage: /enc <none|fernet|rsa|custom:...>', 'warn');
    state.encMode = next;
    saveLocal();
    updateStatus();
    updateSessionCard();
    line(`enc mode set to ${state.encMode}`, 'ok');
    return;
  }
  if (cmd === '/join') {
    const target = parts[1];
    if (!target) return line('Usage: /join <@channel>', 'warn');
    const chan = target.startsWith('@') ? target : `@${target}`;
    return switchTarget(chan);
  }
  if (cmd === '/connect') {
    const peer = parts[1];
    if (!peer) return line('Usage: /connect <peer_id>', 'warn');
    return switchTarget(peer);
  }
  if (cmd === '/listen') {
    line('Web client is firebase-only. /listen is CLI-only.', 'warn');
    return;
  }
  if (cmd === '/load') {
    const arg = parts[1];
    if (!arg || arg === 'on' || arg === 'start') {
      state.autoLoad = true;
      updateStatus();
      line(`Auto-load enabled (${state.autoLoadLimit})`, 'ok');
      return;
    }
    if (arg === 'off' || arg === 'stop') {
      state.autoLoad = false;
      updateStatus();
      line('Auto-load disabled', 'warn');
      return;
    }
    const n = Number(arg);
    if (!Number.isFinite(n) || n < 1) return line('Usage: /load [n|on|off]', 'warn');
    const lim = Math.min(500, Math.floor(n));
    state.autoLoadLimit = lim;
    updateStatus();
    line(`Loading latest ${lim} messages...`, 'sys');
    await loadLatest(lim);
    return;
  }

  line(`Unknown command: ${cmd}`, 'err');
}

async function handleInput() {
  const value = el.cmd.value.trim();
  if (!value) return;
  el.cmd.value = '';

  if (value.startsWith('/')) {
    await runCommand(value);
    return;
  }

  await sendChatMessage(value);
}

async function bootstrapFirebase() {
  if (window.HERMES_FIREBASE_CONFIG) {
    state.cfg = window.HERMES_FIREBASE_CONFIG;
  } else {
    const res = await fetch('/web-config');
    state.cfg = await res.json();
  }

  state.app = initializeApp(state.cfg.firebase_web);
  state.db = getDatabase(state.app);
  state.auth = getAuth(state.app);

  if (!state.userId) {
    const cred = await signInAnonymously(state.auth);
    state.userId = cred.user.uid;
  }
  if (!state.username) state.username = `web-${String(state.userId).slice(0, 6)}`;

  saveLocal();

  state.unsubs.connected = onValue(ref(state.db, '.info/connected'), async (s) => {
    state.connected = !!s.val();
    if (state.connected) {
      try {
        await setPresence();
      } catch {
        line('Presence update failed.', 'warn');
      }
    }
    updateStatus();
    updateSessionCard();
  });

  state.unsubs.presence = onValue(ref(state.db, 'presence'), (s) => {
    const val = s.val() || {};
    const arr = Object.values(val)
      .filter((p) => p && p.id && p.id !== state.userId && p.online)
      .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
    state.peers = arr;
    for (const p of arr) state.chats.add(String(p.id));
    renderPeers();
    renderChats();
    updateStatus();
  });

  // Inbox listener for firebase direct messages pushed to this user id path.
  if (state.unsubs.inbox) state.unsubs.inbox();
  const inboxPath = `messages/${safeKey(state.userId)}`;
  state.unsubs.inbox = onChildAdded(query(ref(state.db, inboxPath), orderByChild('ts'), limitToLast(200)), (snap) => {
    if (!state.autoLoad) return;
    if (state.activeTarget.startsWith('@')) return;
    const msg = snap.val();
    if (!msg || typeof msg !== 'object') return;
    // Show inbox entries while in direct chat contexts.
    messageLine(msg);
  });
}

function wireMenu() {
  el.menuName.value = state.username;
  el.menuSaveName.onclick = async () => {
    const next = el.menuName.value.trim();
    if (!next) return;
    state.username = next;
    saveLocal();
    updateStatus();
    updateSessionCard();
    await setPresence();
    line(`Name set to ${state.username}`, 'ok');
  };
  el.menuOpen.onclick = async () => {
    const target = el.menuOpenTarget.value.trim() || '@broadcast';
    await switchTarget(target.startsWith('@') ? target : target);
    el.menuOverlay.style.display = 'none';
    el.cmd.focus();
  };
  el.menuJoinBtn.onclick = async () => {
    const target = (el.menuJoin.value.trim() || '@broadcast');
    const chan = target.startsWith('@') ? target : `@${target}`;
    await switchTarget(chan);
    el.menuOverlay.style.display = 'none';
    el.cmd.focus();
  };
  el.menuConnectBtn.onclick = async () => {
    const target = el.menuConnect.value.trim();
    if (!target) return;
    await switchTarget(target);
    el.menuOverlay.style.display = 'none';
    el.cmd.focus();
  };
}

async function start() {
  updatePrompt();
  line('Booting Hermes web terminal...', 'sys');
  await bootstrapFirebase();
  wireMenu();
  await switchTarget(state.activeTarget || '@broadcast');
  printHelp();
  updateStatus();
  updateSessionCard();

  el.cmd.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    await handleInput();
  });

  // Keep presence fresh.
  setInterval(() => {
    if (state.connected) setPresence().catch(() => {});
  }, 30000);
}

start().catch((err) => {
  console.error(err);
  line(`Fatal startup error: ${err?.message || err}`, 'err');
});
