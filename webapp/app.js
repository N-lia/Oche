/* OYI Live — Telegram WebApp client.
   Real-time voice (barge-in + backchannels) plus text chat with a live
   agent-activity stream. Talks to /ws/voice and /api/* on the same origin. */

(() => {
  'use strict';

  // ------------------------------------------------------------- Telegram SDK
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      if (tg.setHeaderColor) tg.setHeaderColor('secondary_bg_color');
      if (tg.setBackgroundColor) tg.setBackgroundColor('bg_color');
    } catch (_) { /* older SDK */ }
    applyTheme();
    tg.onEvent('themeChanged', applyTheme);
  }

  function applyTheme() {
    const params = (tg && tg.themeParams) || {};
    const root = document.documentElement;
    const map = {
      '--tg-theme-bg-color': params.bg_color,
      '--tg-theme-secondary-bg-color': params.secondary_bg_color,
      '--tg-theme-text-color': params.text_color,
      '--tg-theme-hint-color': params.hint_color,
      '--tg-theme-link-color': params.link_color,
      '--tg-theme-button-color': params.button_color,
      '--tg-theme-button-text-color': params.button_text_color,
      '--tg-theme-section-bg-color': params.section_bg_color,
    };
    Object.entries(map).forEach(([key, value]) => {
      if (value) root.style.setProperty(key, value);
    });
    const scheme = params.bg_color ? luminance(params.bg_color) : 0;
    root.setAttribute('data-theme', scheme > 0.5 ? 'light' : 'dark');
  }

  function luminance(hex) {
    const m = hex.replace('#', '');
    const v = m.length === 3 ? m.split('').map((c) => c + c).join('') : m;
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16) / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  const haptic = (style = 'light') => {
    try { if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(style); } catch (_) {}
  };

  // ------------------------------------------------------------------- DOM
  const $ = (id) => document.getElementById(id);
  const feed = $('feed');
  const connDot = $('connDot');
  const connText = $('connText');
  const voiceStrip = $('voiceStrip');
  const voiceState = $('voiceState');
  const wave = $('wave');
  const micButton = $('micButton');
  const chatForm = $('chatForm');
  const chatInput = $('chatInput');
  const sendButton = $('sendButton');
  const activity = $('activity');
  const activityText = $('activityText');
  const activityTimer = $('activityTimer');
  const btnSettings = $('btnSettings');
  const btnSound = $('btnSound');
  const sheet = $('sheet');
  const sheetBackdrop = $('sheetBackdrop');
  const modeSeg = $('modeSeg');
  const moodSelect = $('moodSelect');
  const toggleSpeak = $('toggleSpeak');
  const toggleBackchannel = $('toggleBackchannel');
  const btnClear = $('btnClear');
  const engineInfo = $('engineInfo');
  const toastEl = $('toast');

  // ----------------------------------------------------------------- state
  const S = {
    socket: null,
    wsState: 'offline',
    wantSocket: false,
    retry: 0,
    reconnectTimer: null,
    waiters: [],
    turnId: 0,
    interruptedTurnId: -1,
    mode: 'agent',
    mood: 'normal',
    speakReplies: true,
    backchannels: true,
    threadId: 'webapp',
    busy: false,
    speaking: false,
    silenceStartedAt: 0,
    userBubble: null,
    botBubble: null,
    voiceActive: false,
    activityStartedAt: 0,
    activityInterval: null,
  };

  const SPEECH_RMS = 0.012;
  const SILENCE_MS = 900;
  const PREROLL_FRAMES = 16;
  const preroll = [];

  let audioContext = null;
  let micStream = null;
  let workletNode = null;
  let analyser = null;
  let botAudio = null;
  const overlays = new Set();

  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.id) {
    S.threadId = String(tg.initDataUnsafe.user.id);
  }

  // ------------------------------------------------------------- rendering
  function scrollDown() { feed.scrollTop = feed.scrollHeight; }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function addMessage(kind, text) {
    const bubble = el('div', `msg ${kind}`, text);
    feed.appendChild(bubble);
    scrollDown();
    return bubble;
  }

  function addChip(text, isFiller) {
    const chip = el('div', `chip${isFiller ? ' filler' : ''}`, text);
    feed.appendChild(chip);
    scrollDown();
    setTimeout(() => { chip.style.transition = 'opacity .4s'; chip.style.opacity = '0'; }, 2400);
    setTimeout(() => chip.remove(), 2900);
  }

  function addTool(label, state) {
    const id = `tool-${label}`;
    let node = feed.querySelector(`[data-tool="${id}"]`);
    if (!node) {
      node = el('div', 'tool');
      node.dataset.tool = id;
      node.appendChild(el('span', 'tool__dot'));
      node.appendChild(el('span', null, label));
      feed.appendChild(node);
    }
    node.classList.toggle('is-running', state === 'start');
    node.lastChild.textContent = state === 'done' ? `${label} ✓` : label;
    scrollDown();
    return node;
  }

  function setConn(state, text) {
    S.wsState = state;
    connDot.className = `conn-dot is-${state}`;
    if (text) connText.textContent = text;
  }

  function setVoiceState(state, text) {
    voiceState.dataset.state = state;
    voiceState.textContent = text;
  }

  function showActivity(text) {
    activity.hidden = false;
    activityText.textContent = text || 'Working…';
    if (!S.activityStartedAt) {
      S.activityStartedAt = Date.now();
      S.activityInterval = setInterval(() => {
        activityTimer.textContent = `${((Date.now() - S.activityStartedAt) / 1000).toFixed(1)}s`;
      }, 100);
    }
  }

  function hideActivity() {
    activity.hidden = true;
    activityTimer.textContent = '';
    S.activityStartedAt = 0;
    if (S.activityInterval) { clearInterval(S.activityInterval); S.activityInterval = null; }
  }

  let toastTimer = null;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2600);
  }

  // ------------------------------------------------------------ user bubbles
  function upsertUserBubble(text) {
    if (!S.userBubble) S.userBubble = addMessage('user', text);
    else S.userBubble.textContent = text;
    scrollDown();
  }

  function finalizeUserBubble(text) {
    if (S.userBubble) {
      if (text) S.userBubble.textContent = text;
      S.userBubble = null;
    } else if (text) {
      const last = feed.lastElementChild;
      if (!(last && last.classList.contains('user') && last.textContent === text)) {
        addMessage('user', text);
      }
    }
  }

  function appendBotText(delta) {
    if (!S.botBubble) {
      S.botBubble = addMessage('bot', '');
      S.botBubble.classList.add('is-streaming');
    }
    S.botBubble.textContent += delta;
    hideActivity();
    scrollDown();
  }

  function finalizeBotBubble() {
    if (S.botBubble) { S.botBubble.classList.remove('is-streaming'); S.botBubble = null; }
  }

  // ------------------------------------------------------------- websocket
  function wsSend(obj) {
    if (S.socket && S.socket.readyState === WebSocket.OPEN) {
      S.socket.send(JSON.stringify(obj));
    }
  }

  function ensureSocket() {
    if (S.socket && S.socket.readyState === WebSocket.OPEN) return Promise.resolve();
    S.wantSocket = true;
    return new Promise((resolve) => {
      S.waiters.push(resolve);
      if (!S.socket || S.socket.readyState === WebSocket.CLOSED) connect();
    });
  }

  function connect() {
    if (S.socket && (S.socket.readyState === WebSocket.OPEN || S.socket.readyState === WebSocket.CONNECTING)) return;
    setConn('connecting', 'Connecting…');
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${proto}://${location.host}/ws/voice`);
    socket.binaryType = 'arraybuffer';
    S.socket = socket;

    socket.onopen = () => {
      S.retry = 0;
      setConn('online', 'Online');
      wsSend({
        type: 'start',
        sample_rate: 16000,
        mode: S.mode,
        mood: S.mood,
        thread_id: S.threadId,
        speak_text_replies: S.speakReplies,
      });
      S.waiters.splice(0).forEach((resolve) => resolve());
    };

    socket.onmessage = (event) => {
      if (typeof event.data !== 'string') return;
      let msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }
      handleServerMessage(msg);
    };

    socket.onclose = () => {
      setConn('offline', 'Offline');
      S.socket = null;
      if (S.wantSocket) scheduleReconnect();
      if (S.voiceActive) stopVoice();
    };

    socket.onerror = () => { /* onclose handles recovery */ };
  }

  function scheduleReconnect() {
    clearTimeout(S.reconnectTimer);
    const delay = Math.min(1000 * 2 ** S.retry, 15000);
    S.retry += 1;
    S.reconnectTimer = setTimeout(() => { if (S.wantSocket) connect(); }, delay);
  }

  // --------------------------------------------------------------- protocol
  function handleServerMessage(msg) {
    switch (msg.type) {
      case 'ready':
        S.turnId = 0;
        S.interruptedTurnId = -1;
        setConn('online', 'Online');
        break;

      case 'partial':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        if (msg.turn_id) S.turnId = msg.turn_id;
        upsertUserBubble(msg.text);
        if (S.voiceActive) setVoiceState('listening', 'Hearing you…');
        break;

      case 'user_text':
        if (msg.turn_id) S.turnId = msg.turn_id;
        finalizeUserBubble(msg.text);
        S.busy = true;
        showActivity('Thinking…');
        break;

      case 'eou':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        if (msg.turn_id) S.turnId = msg.turn_id;
        finalizeUserBubble(msg.text);
        S.busy = true;
        showActivity('Thinking…');
        break;

      case 'status':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        showActivity(msg.text || 'Working…');
        break;

      case 'tool':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        addTool(msg.label || msg.name, msg.state);
        showActivity(msg.label || 'Working…');
        break;

      case 'backchannel':
        if (!S.backchannels) break;
        addChip(msg.phrase, false);
        if (S.backchannels) playOverlay(msg.url, 0.55);
        break;

      case 'filler':
        if (!S.backchannels) break;
        addChip(msg.phrase, true);
        playOverlay(msg.url, 0.6);
        break;

      case 'bot_text_delta':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        appendBotText(msg.text);
        break;

      case 'bot_audio':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        playBotAudio(msg.audio);
        break;

      case 'interrupted':
        hideActivity();
        stopOverlays();
        if (botAudio) { botAudio.pause(); botAudio = null; }
        finalizeUserBubble();
        S.busy = false;
        if (S.voiceActive) setVoiceState('listening', 'Go ahead…');
        break;

      case 'error':
        hideActivity();
        addMessage('error', msg.message || 'Something went wrong.');
        S.busy = false;
        finalizeBotBubble();
        break;

      case 'turn_end':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        finalizeBotBubble();
        finalizeUserBubble();
        hideActivity();
        S.busy = false;
        if (S.voiceActive) setVoiceState('listening', 'Listening');
        break;

      default:
        break;
    }
  }

  // -------------------------------------------------------------- playback
  function playOverlay(url, volume) {
    if (!url || !S.speakReplies) return;
    const audio = new Audio(url);
    audio.volume = volume;
    overlays.add(audio);
    audio.onended = () => overlays.delete(audio);
    audio.play().catch(() => overlays.delete(audio));
  }

  function stopOverlays() {
    overlays.forEach((a) => a.pause());
    overlays.clear();
  }

  function playBotAudio(base64) {
    stopOverlays();
    if (botAudio) { botAudio.pause(); botAudio = null; }
    if (!S.speakReplies || !base64) {
      wsSend({ type: 'playback_end' });
      return;
    }
    setVoiceState('speaking', 'Speaking…');
    botAudio = new Audio(`data:audio/mpeg;base64,${base64}`);
    botAudio.onended = () => {
      botAudio = null;
      wsSend({ type: 'playback_end' });
      if (S.voiceActive) setVoiceState('listening', 'Listening');
    };
    botAudio.play().catch(() => { wsSend({ type: 'playback_end' }); });
  }

  // ----------------------------------------------------------- audio utils
  function resampleTo16k(float32, inRate) {
    if (inRate === 16000) return float32;
    const ratio = inRate / 16000;
    const outLength = Math.floor(float32.length / ratio);
    const out = new Float32Array(outLength);
    for (let i = 0; i < outLength; i += 1) {
      const pos = i * ratio;
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const a = float32[idx] || 0;
      const b = float32[idx + 1] || a;
      out[i] = a + (b - a) * frac;
    }
    return out;
  }

  function toInt16(float32) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i += 1) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  function rms(int16) {
    let sum = 0;
    for (let i = 0; i < int16.length; i += 1) {
      const v = int16[i] / 32768;
      sum += v * v;
    }
    return int16.length ? Math.sqrt(sum / int16.length) : 0;
  }

  function base64FromInt16(int16) {
    const bytes = new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function prerollBase64() {
    let total = 0;
    for (const frame of preroll) total += frame.length;
    if (!total) return '';
    const merged = new Int16Array(total);
    let offset = 0;
    for (const frame of preroll) { merged.set(frame, offset); offset += frame.length; }
    return base64FromInt16(merged);
  }

  // --------------------------------------------------------------- voice
  function pushPreroll(pcm) {
    preroll.push(pcm);
    while (preroll.length > PREROLL_FRAMES) preroll.shift();
  }

  function onMicFrame(float32) {
    if (!S.voiceActive || !S.socket || S.socket.readyState !== WebSocket.OPEN) return;
    const pcm = toInt16(resampleTo16k(float32, audioContext.sampleRate));
    const level = rms(pcm);
    wave.style.setProperty('--level', level.toFixed(3));
    const isSpeech = level >= SPEECH_RMS;
    const now = performance.now();
    pushPreroll(pcm);

    if (isSpeech && S.busy) bargeIn();

    if (isSpeech) {
      S.silenceStartedAt = 0;
      if (!S.busy) setVoiceState('listening', 'Hearing you…');
    } else if (S.speaking) {
      if (!S.silenceStartedAt) S.silenceStartedAt = now;
      else if (now - S.silenceStartedAt >= SILENCE_MS && !S.busy) {
        S.busy = true;
        wsSend({ type: 'flush' });
        showActivity('Transcribing…');
        setVoiceState('thinking', 'Thinking…');
      }
    }
    S.speaking = isSpeech;

    if (!S.busy || isSpeech) S.socket.send(pcm.buffer);
  }

  function bargeIn() {
    haptic('medium');
    if (botAudio) { botAudio.pause(); botAudio = null; }
    stopOverlays();
    S.interruptedTurnId = S.turnId;
    S.busy = false;
    S.speaking = true;
    S.silenceStartedAt = 0;
    finalizeUserBubble();
    hideActivity();
    setVoiceState('listening', 'Go ahead…');
    wsSend({ type: 'barge_in', preroll: prerollBase64() });
  }

  async function startVoice() {
    if (S.voiceActive) return;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      if (audioContext.state === 'suspended') await audioContext.resume();
      await audioContext.audioWorklet.addModule('/static/mic-worklet.js');

      const source = audioContext.createMediaStreamSource(micStream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);

      workletNode = new AudioWorkletNode(audioContext, 'mic-worklet');
      workletNode.port.onmessage = (event) => onMicFrame(event.data);
      source.connect(workletNode);
      const silent = audioContext.createGain();
      silent.gain.value = 0;
      workletNode.connect(silent);
      silent.connect(audioContext.destination);

      S.voiceActive = true;
      S.busy = false;
      S.speaking = false;
      micButton.classList.add('is-active');
      voiceStrip.hidden = false;
      setVoiceState('listening', 'Listening');
      if (tg && tg.enableClosingConfirmation) tg.enableClosingConfirmation();
      haptic('medium');
      await ensureSocket();
    } catch (err) {
      addMessage('error', `Microphone unavailable: ${err.message || err}`);
      await stopVoice();
    }
  }

  async function stopVoice() {
    S.voiceActive = false;
    S.busy = false;
    S.speaking = false;
    micButton.classList.remove('is-active');
    voiceStrip.hidden = true;
    hideActivity();
    if (workletNode) { workletNode.port.onmessage = null; workletNode.disconnect(); workletNode = null; }
    if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
    if (audioContext) { await audioContext.close(); audioContext = null; analyser = null; }
    if (botAudio) { botAudio.pause(); botAudio = null; }
    stopOverlays();
    if (tg && tg.disableClosingConfirmation) tg.disableClosingConfirmation();
  }

  // --------------------------------------------------------------- controls
  micButton.addEventListener('click', async () => {
    if (S.voiceActive) await stopVoice();
    else await startVoice();
  });

  $('voiceStop').addEventListener('click', () => stopVoice());

  chatForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = '';
    sendButton.disabled = true;
    addMessage('user', text);
    finalizeUserBubble(text);
    haptic('light');
    await ensureSocket();
    showActivity('Thinking…');
    wsSend({ type: 'text', text, speak: S.speakReplies });
  });

  chatInput.addEventListener('input', () => { sendButton.disabled = !chatInput.value.trim(); });

  btnSound.addEventListener('click', () => {
    S.speakReplies = !S.speakReplies;
    toggleSpeak.checked = S.speakReplies;
    btnSound.classList.toggle('is-off', !S.speakReplies);
    wsSend({ type: 'set', speak_text_replies: S.speakReplies });
    toast(S.speakReplies ? 'Spoken replies on' : 'Spoken replies off');
  });

  function openSheet() { sheet.hidden = false; sheetBackdrop.hidden = false; if (tg && tg.BackButton) tg.BackButton.show(); }
  function closeSheet() { sheet.hidden = true; sheetBackdrop.hidden = true; if (tg && tg.BackButton) tg.BackButton.hide(); }
  btnSettings.addEventListener('click', openSheet);
  sheetBackdrop.addEventListener('click', closeSheet);
  if (tg && tg.BackButton) tg.BackButton.onClick(closeSheet);

  modeSeg.addEventListener('click', (event) => {
    const button = event.target.closest('.segment');
    if (!button) return;
    S.mode = button.dataset.mode;
    [...modeSeg.children].forEach((b) => b.classList.toggle('is-active', b === button));
    wsSend({ type: 'set', mode: S.mode });
  });

  moodSelect.addEventListener('change', () => {
    S.mood = moodSelect.value;
    wsSend({ type: 'set', mood: S.mood });
  });

  toggleSpeak.addEventListener('change', () => {
    S.speakReplies = toggleSpeak.checked;
    btnSound.classList.toggle('is-off', !S.speakReplies);
    wsSend({ type: 'set', speak_text_replies: S.speakReplies });
  });

  toggleBackchannel.addEventListener('change', () => { S.backchannels = toggleBackchannel.checked; });

  btnClear.addEventListener('click', () => {
    feed.innerHTML = '';
    addMessage('system', 'Conversation cleared. Start speaking or type a message.');
    closeSheet();
  });

  // --------------------------------------------------------------- health
  async function checkHealth() {
    try {
      const res = await fetch('/api/health', { cache: 'no-store' });
      const data = await res.json();
      const asr = data.asr && data.asr.ready ? '✅ ASR ready' : '⚠️ ASR unavailable';
      const tts = data.tts && data.tts.configured ? '✅ TTS ready' : '⚠️ TTS not configured';
      const clips = (data.backchannels && data.backchannels.cached) || 0;
      engineInfo.innerHTML = `${asr}<br>${tts}<br>${clips} backchannel clips cached<br>v${data.version || '?'}`;
    } catch (_) {
      engineInfo.textContent = 'Service check failed.';
    }
  }

  // ------------------------------------------------------------------ boot
  addMessage('system', 'Hey — talk or type. I stream live and you can interrupt me any time.');
  checkHealth();
  window.addEventListener('beforeunload', () => { S.wantSocket = false; });
})();
