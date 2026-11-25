const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  sessionId: null,
  meeting: null,
  ws: null,
  wsConnected: false,
  segments: [],
  partial: null,
  partialStart: 0,
  messages: [],
  streaming: new Map(),
  lastAssistant: null,
  lastQuestion: "",
  demoPlaying: false,
  mic: null,
  chatLock: false,
  clockAt: performance.now(),
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
function fmtClock(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = String(Math.floor(seconds / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}
function toast(message, isError = false) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 4200);
}
async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try { const data = await resp.json(); detail = data.detail || JSON.stringify(data); } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

document.addEventListener("DOMContentLoaded", init);

async function init() {
  try {
    await openMeeting();
    connectWS();
  } catch (err) {
    toast(`Could not start: ${err.message}`, true);
  }
}

async function openMeeting() {
  try {
    const meeting = await api("/api/meetings", {
      method: "POST",
      body: JSON.stringify({ title: "Otto meeting", source: "demo" }),
    });
    state.sessionId = meeting.id;
    state.meeting = meeting;
    history.replaceState({}, "", `?session=${state.sessionId}`);
  } catch (err) {
    toast(`Could not open meeting: ${err.message}`, true);
  }
}

function connectWS() {
  if (!state.sessionId) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/meetings/${state.sessionId}`);
  state.ws = ws;
  state.wsConnected = false;

  ws.onopen = () => { state.wsConnected = true; };
  ws.onclose = () => { state.wsConnected = false; setTimeout(connectWS, 1800); };
  ws.onerror = () => {};
  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (_) { return; }
    handleWS(msg);
  };
}
function sendWS(payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}
function floatToBase64(frame) {
  const pcm = new Int16Array(frame.length);
  for (let i = 0; i < frame.length; i++) {
    const v = Math.max(-1, Math.min(1, frame[i]));
    pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(binary);
}
function waitForWS(timeoutMs = 6000) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) return Promise.resolve(true);
  return new Promise(resolve => {
    const started = Date.now();
    const check = () => {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) return resolve(true);
      if (Date.now() - started >= timeoutMs) return resolve(false);
      setTimeout(check, 120);
    };
    check();
  });
}
function handleWS(msg) {
  switch (msg.type) {
    case "hello":
      state.config = msg.config;
      state.segments = (msg.state.transcript || []).slice();
      state.partial = msg.state.partial || null;
      
      state.messages = (msg.state.chat || []).filter(m => !String(m.id || "").startsWith("tmp_")).slice();
      state.audio = msg.state.audio || state.audio;
      renderTranscript();
      renderChat();
      break;
    case "status":
      if (msg.audio) {
        state.audio = msg.audio;
        updateAudioUI();
      }
      break;
    case "transcript.segment":
      addSegment(msg.segment);
      break;
    case "transcript.partial":
      state.partial = msg.text;
      state.partialStart = msg.t_start || 0;
      renderTranscript();
      break;
    case "chat.message":
      addMessage(msg.message);
      break;
    case "chat.start":
      state.streaming.set(msg.request_id, {
        requestId: msg.request_id, text: "", engine: msg.engine, question: msg.question,
      });
      renderChat();
      break;
    case "chat.delta": {
      const stream = state.streaming.get(msg.request_id);
      if (!stream) return;
      stream.text += msg.delta;
      updateStreamingBubble(msg.request_id);
      break;
    }
    case "chat.done": {
      state.streaming.delete(msg.request_id);
      addMessage(msg.message);
      state.lastAssistant = msg.message;
      break;
    }
    case "chat.error":
      state.streaming.delete(msg.request_id);
      renderChat();
      toast(msg.error || "Otto could not answer", true);
      break;
    default:
      break;
  }
}

function updateAudioUI() {
  const audio = state.audio || {};
  const badge = $("#audioBadge");
  if (badge) {
    badge.textContent = audio.status || "idle";
    badge.className = `audio-badge ${["streaming","playing"].includes(audio.status) ? "live" : ""} ${audio.status === "error" ? "error" : ""}`.trim();
  }
  const label = $("#waveLabel");
  if (label) {
    if (audio.status === "streaming" || audio.status === "playing") {
      label.textContent = `audio live · ${fmtClock(audio.clock_s)}`;
    } else if (audio.status === "ended" || audio.status === "stopped") {
      label.textContent = `audio ${audio.status}`;
    } else if (audio.status === "error") {
      label.textContent = "audio error";
    }
  }
}

function addSegment(segment) {
  if (!segment) return;
  if (state.segments.some(s => s.id === segment.id)) return;
  state.segments.push(segment);
  state.segments.sort((a, b) => a.t_start - b.t_start);
  renderTranscript();
}
function renderTranscript() {
  const el = $("#transcript");
  if (!state.segments.length && !state.partial) {
    el.innerHTML = `<div class="empty">
      <div class="empty-icon">◉</div>
      <h3>No audio yet</h3>
      <p>Play the sample meeting or use your microphone to start.</p>
      <div class="empty-actions">
        <button class="btn mint" data-action="demo">▶ Run demo</button>
        <button class="btn" data-action="mic">🎙 Use mic</button>
      </div>
    </div>`;
    return;
  }
  const rows = state.segments.map(seg => {
    const name = seg.speaker || "Speaker A";
    const isB = /b/i.test(name.replace(/speaker|developer/g, ""));
    const initials = isB ? "B" : "A";
    return `<div class="seg ${isB ? "speaker-b" : "speaker-a"}" data-seg="${esc(seg.id)}">
      <div class="seg-head">
        <span class="spk">${initials}</span>
        <span class="speaker">${esc(name)}</span>
        <span class="ts">${fmtClock(seg.t_start)}</span>
      </div>
      <div class="txt">${esc(seg.text)}</div>
    </div>`;
  }).join("");
  const partial = state.partial
    ? `<div class="seg partial-row">
      <div class="seg-head">
        <span class="spk">···</span>
        <span class="speaker">typing…</span>
        <span class="ts">${fmtClock(state.partialStart)}</span>
      </div>
      <div class="txt">${esc(state.partial)}<span class="partial-tag">partial</span></div>
    </div>`
    : "";
  el.innerHTML = rows + partial;
  el.scrollTop = el.scrollHeight;
}

function addMessage(message) {
  if (!message) return;
  if (state.messages.some(m => m.id === message.id)) return;
  
  state.messages.push(message);
  if (message.role === "assistant") state.lastAssistant = message;
  renderChat();
  scrollChatToEnd();
}
function scrollChatToEnd() {
  const list = $("#chat");
  if (list) list.scrollTop = list.scrollHeight;
}
function renderChat() {
  const el = $("#chat");
  const messages = state.messages.map(messageEl).join("");
  const streaming = Array.from(state.streaming.values()).map(streamEl).join("");
  if (!messages && !streaming) {
    el.innerHTML = `<div class="empty chat-empty">
      <div class="empty-icon">O</div>
      <h3>Ask Otto about the meeting</h3>
      <p>Ask a question. Otto answers from the live audio transcript and the project manual.</p>
    </div>`;
    return;
  }
  el.innerHTML = messages + streaming;
}
function messageEl(message) {
  if (message.role === "user") {
    return `<div class="msg user" data-msg="${esc(message.id)}">
      <div class="bubble-wrap"><div class="bubble">${esc(message.text)}</div></div>
    </div>`;
  }
  return `<div class="msg assistant" data-msg="${esc(message.id)}">
    <div class="mini-avatar">O</div>
    <div class="bubble-wrap">
      <div class="bubble">${esc(message.text)}</div>
      <div class="msg-meta">
        <span class="tag">${esc(message.engine || "otto")}</span>
      </div>
    </div>
  </div>`;
}
function streamEl(stream) {
  return `<div class="msg assistant" data-stream="${esc(stream.requestId)}">
    <div class="mini-avatar">O</div>
    <div class="bubble-wrap">
      <div class="bubble streaming">${esc(stream.text || "…")}</div>
      <div class="msg-meta"><span class="tag">${esc(stream.engine || "otto")}</span><span>answering…</span></div>
    </div>
  </div>`;
}
let streamRenderQueued = false;
function updateStreamingBubble(requestId) {
  if (streamRenderQueued) return;
  streamRenderQueued = true;
  requestAnimationFrame(() => {
    streamRenderQueued = false;
    const stream = state.streaming.get(requestId);
    const bubble = document.querySelector(`[data-stream="${requestId}"] .bubble`);
    if (stream && bubble) {
      bubble.innerHTML = renderRich(stream.text || "…");
      const list = $("#chat");
      if (list.scrollHeight - list.scrollTop - list.clientHeight < 220) list.scrollTop = list.scrollHeight;
    }
  });
}
function renderRich(text) {
  let safe = esc(text || "");
  safe = safe.replace(/\[(KB\s+p\.?\s*\d+[^\]]*|meeting\s+\d{1,2}:\d{2})\]/gi, (match, inner) => {
    const isKb = inner.toLowerCase().startsWith("kb");
    return `<span class="cite ${isKb ? "kb" : "meeting"}" data-cite="${esc(inner)}">${esc(match)}</span>`;
  });
  safe = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");
  safe = safe.replace(/\n/g, "<br>");
  return safe;
}
function sendChat(text) {
  text = (text || "").trim();
  if (!text) return;
  if (!state.sessionId) { toast("Meeting not ready", true); return; }
  if (state.chatLock) { toast("Otto is already answering — please wait", true); return; }
  state.chatLock = true;
  state.lastQuestion = text;

  const inp = $("#chatInput");
  if (inp) inp.value = "";

  
  const userMsgId = "tmp_" + Date.now();
  addMessage({ id: userMsgId, role: "user", text: text, created_at: Date.now() / 1000 });

  
  api(`/api/meetings/${state.sessionId}/chat`, {
    method: "POST",
    body: JSON.stringify({ text, include_debug: true }),
  }).then(message => {
    state.chatLock = false;
    addMessage(message);
    state.lastAssistant = message;
  }).catch(err => {
    state.chatLock = false;
    toast(err.message, true);
  });
}

async function runDemo() {
  if (state.demoPlaying) { await stopDemo(); return; }
  if (state.mic) await stopMic();
  if (!state.sessionId) {
    try {
      const meeting = await api("/api/meetings", {
        method: "POST",
        body: JSON.stringify({ title: "Otto meeting", source: "demo" }),
      });
      state.sessionId = meeting.id;
      state.meeting = meeting;
      history.replaceState({}, "", `?session=${state.sessionId}`);
      connectWS();
    } catch (err) { toast("Could not create meeting: " + err.message, true); return; }
  }
  try {
    const res = await api(`/api/meetings/${state.sessionId}/demo`, { method: "POST" });
    state.demoPlaying = true;
    const audio = $("#roomAudio");
    audio.src = res.playback_url;
    audio.currentTime = 0;
    try { await audio.play(); } catch (_) { toast("Browser blocked autoplay — click the page first, then try again", true); }
    const btn = $("#sampleBtn");
    if (btn) btn.innerHTML = `<span class="play">■</span> Stop sample`;
    toast("Playing sample meeting…");
  } catch (err) { toast(err.message, true); }
}
async function stopDemo() {
  state.demoPlaying = false;
  const audio = $("#roomAudio");
  try { audio.pause(); } catch (_) {}
  await stopAudioServer();
  const btn = $("#sampleBtn");
  if (btn) btn.innerHTML = `▶ Sample meeting`;
}
async function stopAudioServer() {
  if (!state.sessionId) return;
  try { await api(`/api/meetings/${state.sessionId}/audio/stop`, { method: "POST" }); } catch (_) {}
  state.audio.status = "stopped";
  updateAudioUI();
}

async function startMic() {
  if (state.mic) return;
  if (state.demoPlaying) await stopDemo();
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast("Microphone needs a secure page (HTTPS or localhost)", true);
    return;
  }
  if (!await waitForWS()) {
    toast("Otto is not connected yet — try again", true);
    return;
  }
  let stream = null;
  let ctx = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    ctx = new Ctx();
    await ctx.audioWorklet.addModule("/static/audio-worklet.js");
    if (ctx.state === "suspended") await ctx.resume();
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "otto-capture");
    node.port.onmessage = (event) => {
      const frame = event.data;
      if (frame && frame.length) sendWS({ type: "audio", pcm: floatToBase64(frame) });
    };
    source.connect(node);
    state.mic = { stream, ctx, source, node };
    sendWS({ type: "control", action: "start_mic" });
    const btn = $("#micBtn");
    if (btn) {
      btn.innerHTML = `<span class="play">■</span> Stop mic`;
      btn.classList.add("recording");
    }
    toast("Listening to your microphone…");
  } catch (err) {
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (ctx) { try { await ctx.close(); } catch (_) {} }
    toast(`Microphone unavailable: ${err.message}`, true);
  }
}
async function stopMic() {
  const mic = state.mic;
  state.mic = null;
  if (mic) {
    mic.node.port.onmessage = null;
    try { mic.source.disconnect(); } catch (_) {}
    try { mic.node.disconnect(); } catch (_) {}
    mic.stream.getTracks().forEach(track => track.stop());
    try { await mic.ctx.close(); } catch (_) {}
  }
  await stopAudioServer();
  const btn = $("#micBtn");
  if (btn) {
    btn.innerHTML = `🎙 Use microphone`;
    btn.classList.remove("recording");
  }
}
function toggleMic() {
  if (state.mic) stopMic();
  else startMic();
}

document.querySelectorAll("#quickRow button").forEach(btn => {
  btn.addEventListener("click", () => sendChat(btn.dataset.q));
});
const chatInput = $("#chatInput");
if (chatInput) {
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat(chatInput.value);
    }
  });
}

const sampleBtn = $("#sampleBtn");
if (sampleBtn) sampleBtn.addEventListener("click", runDemo);
const micBtn = $("#micBtn");
if (micBtn) micBtn.addEventListener("click", toggleMic);
const transcriptEl = $("#transcript");
if (transcriptEl) {
  transcriptEl.addEventListener("click", (e) => {
    const demoBtn = e.target.closest('[data-action="demo"]');
    const micAction = e.target.closest('[data-action="mic"]');
    if (demoBtn) runDemo();
    else if (micAction) startMic();
  });
}

setInterval(() => {
  let clock = Number(state.audio?.clock_s || 0);
  if (state.audio?.status === "streaming" || state.audio?.status === "playing") {
    clock += (performance.now() - state.clockAt) / 1000;
  } else if (state.segments.length) {
    clock = Math.max(clock, state.segments[state.segments.length - 1].t_end || 0);
  }
  const clockEl = $("#meetingClock");
  if (clockEl) clockEl.textContent = fmtClock(clock);
  state.clockAt = performance.now();
}, 250);
