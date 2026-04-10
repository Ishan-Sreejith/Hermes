const input = document.getElementById('input');
const send = document.getElementById('send');
const messages = document.getElementById('messages');
const status = document.getElementById('status');

let lastTs = 0;
let busy = false;
let theme = localStorage.getItem('theme') || 'light';
let encMode = localStorage.getItem('encMode') || 'none';
let currentChannel = '#broadcast';
let errorBox = null;
let firebaseConfig = null;
let transportState = null;

function clearMessages() {
  if (messages) messages.innerHTML = '';
}

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function getEncBadge(enc) {
  const badges = {
    'none': '📭',
    'fernet': '🔑',
    'rsa': '🔒',
  };
  if (enc && enc.startsWith('custom:')) {
    return '⚙️';
  }
  return badges[enc] || '❓';
}

function setTheme(newTheme) {
  theme = newTheme;
  document.body.style.background = theme === 'dark' ? '#222' : '#f5f5f5';
  document.body.style.color = theme === 'dark' ? '#eee' : '#222';
  localStorage.setItem('theme', theme);
}

function setEncMode(mode) {
  encMode = mode;
  localStorage.setItem('encMode', mode);
  const node = document.getElementById('enc-mode');
  if (node) node.textContent = mode;
}

function showError(msg) {
  if (!errorBox) {
    errorBox = document.createElement('div');
    errorBox.style.position = 'fixed';
    errorBox.style.top = '10px';
    errorBox.style.right = '10px';
    errorBox.style.background = '#e74c3c';
    errorBox.style.color = 'white';
    errorBox.style.padding = '0.5rem 1rem';
    errorBox.style.borderRadius = '4px';
    errorBox.style.zIndex = 1000;
    document.body.appendChild(errorBox);
  }
  errorBox.textContent = msg;
  errorBox.style.display = 'block';
  setTimeout(() => { errorBox.style.display = 'none'; }, 3000);
}

function selectChannel(name) {
  currentChannel = name;
  const node = document.getElementById('current-channel');
  if (node) node.textContent = name;
  clearMessages();
  lastTs = 0;
  refresh();
}

function renderSidebar(peers, channels) {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  sidebar.innerHTML = '';
  const chGroup = document.createElement('div');
  chGroup.className = 'channel-group';
  chGroup.innerHTML = '<h3>Channels</h3>';
  for (const ch of channels) {
    const item = document.createElement('div');
    item.className = 'channel-item';
    item.textContent = ch;
    item.onclick = () => selectChannel(ch);
    chGroup.appendChild(item);
  }
  sidebar.appendChild(chGroup);
  const peerGroup = document.createElement('div');
  peerGroup.className = 'channel-group';
  peerGroup.innerHTML = '<h3>Direct</h3>';
  for (const p of peers) {
    const item = document.createElement('div');
    item.className = 'channel-item';
    item.textContent = p.name || p.peer_id;
    item.onclick = () => selectChannel(p.peer_id);
    peerGroup.appendChild(item);
  }
  sidebar.appendChild(peerGroup);
  const info = document.createElement('div');
  info.style.marginTop = '1.5rem';
  info.innerHTML = [
    `<div><b>Transport:</b> <span id="transport-mode">${transportState?.transport_mode || 'relay'}</span></div>`,
    `<div><b>Direct:</b> <span id="direct-port">${transportState?.direct_port || '—'}</span></div>`,
    `<div><b>UDP:</b> <span id="udp-port">${transportState?.udp_port || '—'}</span></div>`,
    `<div><b>Firebase:</b> <span id="firebase-state">${firebaseConfig?.cloud?.enabled ? 'on' : 'off'}</span></div>`,
  ].join('');
  sidebar.appendChild(info);
  const encDiv = document.createElement('div');
  encDiv.style.marginTop = '1rem';
  encDiv.innerHTML = '<b>Encryption:</b> <span id="enc-mode">' + encMode + '</span>';
  ['none', 'fernet', 'rsa'].forEach(mode => {
    const btn = document.createElement('button');
    btn.textContent = mode;
    btn.onclick = () => setEncMode(mode);
    encDiv.appendChild(btn);
  });
  sidebar.appendChild(encDiv);
  const themeBtn = document.createElement('button');
  themeBtn.textContent = theme === 'dark' ? '🌙 Dark' : '☀️ Light';
  themeBtn.onclick = () => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
    themeBtn.textContent = theme === 'dark' ? '🌙 Dark' : '☀️ Light';
  };
  sidebar.appendChild(themeBtn);
}

async function fetchPeersAndChannels() {
  try {
    const peersRes = await fetch('/peers');
    const peers = peersRes.ok ? await peersRes.json() : [];
    const channels = ['#broadcast', '#test'];
    renderSidebar(peers, channels);
  } catch {}
}

async function fetchFirebaseConfig() {
  try {
    const res = await fetch('/firebase-config');
    if (!res.ok) return;
    firebaseConfig = await res.json();
    const host = document.getElementById('hosting-state');
    if (host) {
      host.textContent = firebaseConfig.cloud.hosting_enabled ? `Firebase Hosting: ${firebaseConfig.cloud.hosting_site || 'enabled'}` : 'Firebase Hosting: disabled';
    }
  } catch {}
}

async function fetchTransportStatus() {
  try {
    const res = await fetch('/status');
    if (!res.ok) return;
    transportState = await res.json();
    if (status) {
      const mode = transportState.connected ? transportState.last_transport || transportState.transport_mode : 'offline';
      status.textContent = transportState.connected ? `✓ ${mode}` : '⚠ Disconnected';
      status.style.color = transportState.connected ? '#27ae60' : '#e74c3c';
    }
  } catch {
    if (status) {
      status.textContent = '⚠ Error';
      status.style.color = '#e74c3c';
    }
  }
}

function appendMessage(m) {
  if (!messages) return;
  if (currentChannel !== '*' && m.channel && m.channel !== currentChannel && m.to !== currentChannel && m.from_id !== currentChannel) return;
  const row = document.createElement('div');
  const ts = document.createElement('span');
  ts.textContent = `[${formatTime(m.ts || 0)}] `;
  ts.style.color = '#7f8c8d';
  ts.style.fontSize = '0.85rem';
  const sender = document.createElement('strong');
  sender.textContent = `${m.from_name || 'unknown'}: `;
  sender.style.color = '#2c3e50';
  const badge = document.createElement('span');
  badge.textContent = `${getEncBadge(m.enc)} `;
  badge.style.fontSize = '0.9rem';
  const body = document.createElement('span');
  body.textContent = m.body || '';
  body.style.color = '#2c3e50';
  row.append(ts, badge, sender, body);
  messages.appendChild(row);
}

async function updateStatus() {
  await fetchTransportStatus();
}

async function refresh() {
  if (!messages) return;
  try {
    const res = await fetch(`/messages?since=${encodeURIComponent(lastTs)}`);
    if (!res.ok) return;
    const data = await res.json();
    for (const m of data) {
      appendMessage(m);
      lastTs = Math.max(lastTs, Number(m.ts || 0));
    }
    messages.scrollTop = messages.scrollHeight;
  } catch {}
}

async function sendMessage() {
  if (!input || !send) return;
  const body = input.value.trim();
  if (!body || busy) return;
  busy = true;
  send.disabled = true;
  try {
    const res = await fetch('/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body, ts: Date.now() / 1000, channel: currentChannel, enc: encMode }),
    });
    if (res.ok) {
      input.value = '';
    } else {
      showError('Could not send message');
    }
  } finally {
    busy = false;
    send.disabled = false;
    input?.focus();
    refresh();
  }
}

send?.addEventListener('click', sendMessage);
input?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') sendMessage();
});

input?.focus();
clearMessages();
updateStatus();
setInterval(refresh, 500);
setInterval(updateStatus, 2000);
setInterval(fetchPeersAndChannels, 2000);
setInterval(fetchFirebaseConfig, 4000);
fetchPeersAndChannels();
fetchFirebaseConfig();
refresh();
setTheme(theme);
setEncMode(encMode);
