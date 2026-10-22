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
      // The mascot lives on white: keep the Telegram chrome white too.
      if (tg.setHeaderColor) tg.setHeaderColor('#ffffff');
      if (tg.setBackgroundColor) tg.setBackgroundColor('#ffffff');
    } catch (_) { /* older SDK */ }
  }

  const haptic = (style = 'light') => {
    try { if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(style); } catch (_) {}
  };

  // ------------------------------------------------------------------- DOM
  const $ = (id) => document.getElementById(id);
  const feed = $('feed');
  const connDot = $('connDot');
  const stageText = $('stageText');
  const micButton = $('micButton');
  const chatForm = $('chatForm');
  const chatInput = $('chatInput');
  const sendButton = $('sendButton');
  const btnSettings = $('btnSettings');
  const sheet = $('sheet');
  const sheetBackdrop = $('sheetBackdrop');
  const modeSeg = $('modeSeg');
  const moodSelect = $('moodSelect');
  const toggleSpeak = $('toggleSpeak');
  const toggleBackchannel = $('toggleBackchannel');
  const toggleHalfDuplex = $('toggleHalfDuplex');
  const btnClear = $('btnClear');
  const engineInfo = $('engineInfo');
  const toastEl = $('toast');

  // The mascot: one purple blob that blinks, looks around and talks.
  const mascot = window.createMascot ? window.createMascot($('mascot')) : null;

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
    duplex: 'full',
    smartTurn: false,
    userBubble: null,
    botBubble: null,
    voiceActive: false,
    activityStartedAt: 0,
    activityInterval: null,
  };

  const PREROLL_FRAMES = 16;
  // Ring entries are { pcm: Int16Array, sent: bool }: `sent` marks frames the
  // turn taker already streamed so preroll seeds never double-feed the ASR.
  const preroll = [];
  let turnTaker = null;

  let audioContext = null;
  let micStream = null;
  let workletNode = null;
  let analyser = null;
  let botAudio = null;
  // Sentence-streamed replies arrive as a frame sequence; the queue holds
  // frames that finished synthesizing while an earlier frame is still on air.
  let audioQueue = [];
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
    connDot.className = `stage__dot is-${state}`;
    if (text) stageText.textContent = text;
  }

  function setCaption(text) {
    stageText.textContent = text || '';
  }

  // The mascot mirrors what the session is doing: idle, listening, thinking
  // or speaking (with the mouth moving while the agent talks).
  function setVoiceState(state, text) {
    if (text) setCaption(text);
    if (!mascot) return;
    if (state === 'speaking') mascot.setState('speaking');
    else if (state === 'thinking') mascot.setState('thinking');
    else if (state === 'holding') mascot.setState('listening');
    else mascot.setState(S.voiceActive ? 'listening' : 'idle');
  }

  function showActivity(text) {
    setCaption(text || 'Working…');
    if (mascot) mascot.setState('thinking');
    if (!S.activityStartedAt) {
      S.activityStartedAt = Date.now();
      S.activityInterval = setInterval(() => {
        const seconds = ((Date.now() - S.activityStartedAt) / 1000).toFixed(1);
        setCaption(`${text || 'Working…'} ${seconds}s`);
      }, 100);
    }
  }

  function hideActivity() {
    S.activityStartedAt = 0;
    if (S.activityInterval) { clearInterval(S.activityInterval); S.activityInterval = null; }
    if (mascot) mascot.setState(S.voiceActive ? 'listening' : 'idle');
    setCaption(S.voiceActive ? 'Listening' : 'Ready');
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
      if (mascot) mascot.pulse();
    }
    S.botBubble.textContent += delta;
    hideActivity();
    scrollDown();
  }

  function finalizeBotBubble() {
    if (S.botBubble) { S.botBubble.classList.remove('is-streaming'); S.botBubble = null; }
  }

  // The agent replies in Telegram HTML: render it client-side with a tiny
  // allowlist, then let pele.mount turn any mermaid code block into a live
  // SVG diagram inside the bubble.
  function sanitizeBotHtml(htmlText) {
    const allowed = new Set(['B', 'I', 'U', 'S', 'STRONG', 'EM', 'DEL', 'INS', 'CODE', 'PRE', 'A', 'BLOCKQUOTE', 'BR']);
    const host = document.createElement('div');
    host.innerHTML = htmlText;
    const walk = (node) => {
      [...node.children].forEach((child) => {
        walk(child);
        if (!allowed.has(child.tagName)) {
          child.replaceWith(...child.childNodes);
          return;
        }
        [...child.attributes].forEach((attr) => child.removeAttribute(attr.name));
        if (child.tagName === 'A' && (/^(https?:|mailto:)/i.test(child.getAttribute('href') || ''))) {
          child.setAttribute('href', child.getAttribute('href'));
          child.setAttribute('target', '_blank');
          child.setAttribute('rel', 'noopener');
        }
        if (child.tagName === 'CODE') {
          const cls = child.getAttribute('class') || '';
          if (/^language-[a-z0-9_+\-]+$/i.test(cls)) child.setAttribute('class', cls);
        }
      });
    };
    walk(host);
    return host;
  }

  function mountMermaidDiagrams(container) {
    if (!window.pele || typeof window.pele.mount !== 'function') return;
    // The server canonicalizes all alias shapes into language-mermaid
    // blocks; blocks that somehow arrived class-less (stale cache, old
    // message) still get mounted when the body opens with a diagram
    // keyword, so a diagram never falls back to dead code text.
    container.querySelectorAll('pre > code:not([class])').forEach((code) => {
      if (/^\s*(?:flowchart|sequenceDiagram|stateDiagram|classDiagram|erDiagram|graph(?:[ \t]+(?:TD|TB|LR|RL|BT))?|mindmap|pie|gantt|journey|timeline)\b/i.test(code.textContent)) {
        code.className = 'language-mermaid';
      }
    });
    container.querySelectorAll('pre > code.language-mermaid').forEach((code) => {
      const pre = code.closest('pre');
      if (!pre) return;
      const source = code.textContent.trim();
      if (!source) return;
      const holder = document.createElement('div');
      holder.className = 'diagram';
      holder.setAttribute('aria-label', 'Diagram');
      holder.dataset.source = source;
      pre.replaceWith(holder);
      window.pele.mount(holder, source, { maxWidth: Math.max(300, container.clientWidth || 340) });
    });
  }

  function renderBotHtml(htmlText) {
    if (!S.botBubble) {
      S.botBubble = addMessage('bot', '');
    }
    S.botBubble.classList.remove('is-streaming');
    S.botBubble.innerHTML = '';
    S.botBubble.appendChild(sanitizeBotHtml(htmlText));
    mountMermaidDiagrams(S.botBubble);
    scrollDown();
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
        S.smartTurn = Boolean(msg.smart_turn);
        if (turnTaker) turnTaker.updateConfig({ silenceMs: S.smartTurn ? 500 : 750 });
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
        if (msg.verdict === 'continuation') {
          // "mm-hmm"/"ok" are not requests: the continuation message removes
          // the bubble and shows a chip instead.
          break;
        }
        if (msg.verdict === 'empty') {
          if (S.userBubble) { S.userBubble.remove(); S.userBubble = null; }
          break;
        }
        finalizeUserBubble(msg.text);
        S.busy = true;
        showActivity('Thinking…');
        break;

      case 'continuation':
        // The transcript was an acknowledgement. Resume the ducked reply (if
        // one is playing) instead of answering the acknowledgement itself.
        unduckBotAudio();
        if (S.userBubble) { S.userBubble.remove(); S.userBubble = null; }
        addChip(msg.text || 'mm-hmm', true);
        if (!botAudio) {
          S.busy = false;
          hideActivity();
        }
        if (S.voiceActive) {
          setVoiceState(botAudio ? 'speaking' : 'listening', botAudio ? 'Speaking…' : 'Listening');
        }
        break;

      case 'resume':
        // The "barge-in" turned out to be noise; let the ducked reply resume.
        unduckBotAudio();
        if (S.userBubble) { S.userBubble.remove(); S.userBubble = null; }
        if (!botAudio) { S.busy = false; hideActivity(); }
        if (S.voiceActive) {
          setVoiceState(botAudio ? 'speaking' : 'listening', botAudio ? 'Speaking…' : 'Listening');
        }
        break;

      case 'barge_pending':
        // Audio is already ducked locally; the transcript will decide whether
        // this was a continuer or a real interruption.
        break;

      case 'turn_hold':
        // Smart Turn heard a pause that may be mid-thought: keep the turn
        // open instead of answering a half sentence.
        hideActivity();
        if (S.voiceActive) setVoiceState('listening', 'Go on…');
        break;

      case 'turn_resumed':
        // The user kept going after a held pause; same turn, no new bubble.
        hideActivity();
        if (S.voiceActive) setVoiceState('listening', 'Hearing you…');
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

      case 'bot_text_final':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        renderBotHtml(msg.text || '');
        break;

      case 'bot_audio':
        if (msg.turn_id && msg.turn_id <= S.interruptedTurnId) break;
        queueBotAudio(msg);
        break;

      case 'interrupted':
        hideActivity();
        stopOverlays();
        stopBotAudio();
        S.interruptedTurnId = msg.turn_id || S.turnId;
        finalizeUserBubble();
        S.busy = false;
        if (S.voiceActive) setVoiceState('listening', 'Go ahead…');
        break;

      case 'error':
        hideActivity();
        toast(msg.message || 'Something went wrong.');
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
  // A tiny analyser on the bot's audio drives the mascot's mouth: the louder
  // the agent, the wider it opens.
  let playbackContext = null;
  let levelMeter = null;
  let levelTimer = 0;

  function levelMeterFor(audio) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!playbackContext || playbackContext.state === 'closed') {
        playbackContext = new Ctx();
      }
      if (playbackContext.state === 'suspended') {
        playbackContext.resume().catch(() => {});
      }
      const source = playbackContext.createMediaElementSource(audio);
      const analyser = playbackContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      analyser.connect(playbackContext.destination);
      return analyser;
    } catch (_) {
      // Some browsers refuse to route a media element through Web Audio; the
      // mascot then falls back to its synthetic envelope.
      return null;
    }
  }

  function startLevelMeter(audio) {
    stopLevelMeter();
    const analyser = levelMeterFor(audio);
    if (!analyser) return;
    levelMeter = analyser;
    const buffer = new Uint8Array(analyser.fftSize);
    levelTimer = window.setInterval(() => {
      if (!levelMeter) return;
      levelMeter.getByteTimeDomainData(buffer);
      let sum = 0;
      for (let i = 0; i < buffer.length; i += 1) {
        const v = (buffer[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / buffer.length);
      if (mascot) mascot.setLevel(Math.min(1, rms * 3.4));
    }, 55);
  }

  function stopLevelMeter() {
    if (levelTimer) { window.clearInterval(levelTimer); levelTimer = 0; }
    levelMeter = null;
    if (mascot) mascot.setLevel(0);
  }

  function playOverlay(url, volume) {
    if (!url || !S.speakReplies) return;
    const audio = new Audio(url);
    audio.volume = volume;
    overlays.add(audio);
    audio.onended = () => overlays.delete(audio);
    audio.play().catch(() => overlays.delete(audio));
    if (mascot) mascot.pulse();
  }

  function stopOverlays() {
    overlays.forEach((a) => a.pause());
    overlays.clear();
  }

  function queueBotAudio(msg) {
    if (typeof msg.frame !== 'number') {
      // Whole-file reply (Chatterbox/CosyVoice, REST echo): single shot.
      playBotAudio(msg.audio, msg.mime, null);
      return;
    }
    // Streaming is ordered on the WebSocket, so frames append in order. If
    // a frame is on air the queue just holds this one; the ended handler
    // pulls the next.
    audioQueue.push(msg);
    if (!botAudio) playNextFrame();
  }

  function playNextFrame() {
    const next = audioQueue.shift();
    if (next) playBotAudio(next.audio, next.mime, next);
  }

  function playBotAudio(base64, mime, frame) {
    stopOverlays();
    if (botAudio) { botAudio.pause(); botAudio = null; }
    const final = !frame || frame.final !== false;
    if (!S.speakReplies || !base64) {
      if (final) wsSend({ type: 'playback_end' });
      else playNextFrame();
      return;
    }
    setVoiceState('speaking', 'Speaking…');
    if (mascot) mascot.setSpeaking(true);
    const taker = ensureTurnTaker();
    // Only the FIRST frame of an output stream opens the turn on the server
    // and resets the echo guard: repeated starts would re-arm the barge
    // window and freeze the taker mid-sentence.
    if (taker.state !== 'agent_speaking' && taker.state !== 'barge_pending' && taker.state !== 'interrupted') {
      taker.agentOutputStart();
      wsSend({ type: 'playback_start' });
    }
    botAudio = new Audio(`data:${mime || 'audio/mpeg'};base64,${base64}`);
    botAudio.onended = () => {
      botAudio = null;
      stopLevelMeter();
      if (final) {
        if (mascot) mascot.setSpeaking(false);
        taker.agentOutputEnd();
        wsSend({ type: 'playback_end' });
        if (S.voiceActive) setVoiceState('listening', 'Listening');
      } else {
        playNextFrame();
      }
    };
    botAudio.play().then(() => startLevelMeter(botAudio)).catch(() => {
      stopLevelMeter();
      if (final) {
        if (mascot) mascot.setSpeaking(false);
        taker.agentOutputEnd();
        wsSend({ type: 'playback_end' });
      } else {
        playNextFrame();
      }
    });
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
    // Only frames the client has NOT already streamed: the turn taker sends
    // audio directly during speech/barge, and re-seeding those frames made
    // the ASR transcript repeat the onset words.
    const unsent = preroll.filter((entry) => !entry.sent);
    let total = 0;
    for (const entry of unsent) total += entry.pcm.length;
    if (!total) return '';
    const merged = new Int16Array(total);
    let offset = 0;
    for (const entry of unsent) { merged.set(entry.pcm, offset); offset += entry.pcm.length; }
    return base64FromInt16(merged);
  }

  // ---------------------------------------------------------- turn-taking
  function onTurnEvent(event) {
    switch (event.type) {
      case 'user_speech.started':
        if (S.voiceActive) {
          setVoiceState('listening', 'Hearing you…');
          wsSend({ type: 'speech_start', preroll: prerollBase64() });
        }
        break;

      case 'user_speech.ended':
        if (S.voiceActive) {
          showActivity('Transcribing…');
          setVoiceState('thinking', 'Thinking…');
          wsSend({ type: 'speech_end' });
        }
        break;

      case 'barge_in.requested':
        onBargeIn();
        break;

      case 'output.duck_requested':
        duckBotAudio();
        break;

      case 'output.cancel_requested':
        stopBotAudio();
        break;

      case 'input.duplex_hold':
        if (S.voiceActive) setVoiceState('holding', 'Mic held while OYI speaks');
        break;

      default:
        break;
    }
  }

  function ensureTurnTaker() {
    if (!turnTaker) {
      turnTaker = window.createTurnTaker({
        config: {
          halfDuplex: { preventWhileAgentSpeaking: S.duplex === 'half' },
          silenceMs: S.smartTurn ? 500 : 750,
        },
        onEvent: onTurnEvent,
      });
    }
    return turnTaker;
  }

  // --------------------------------------------------------------- voice
  function pushPreroll(pcm) {
    preroll.push({ pcm, sent: false });
    while (preroll.length > PREROLL_FRAMES) preroll.shift();
  }

  function markPrerollSent() {
    // Another frame of the same audio went to the socket directly; the newest
    // unsent entry corresponds to it.
    for (let i = preroll.length - 1; i >= 0; i -= 1) {
      if (!preroll[i].sent) {
        preroll[i].sent = true;
        break;
      }
    }
  }

  function onMicFrame(float32) {
    if (!S.voiceActive || !S.socket || S.socket.readyState !== WebSocket.OPEN) return;
    const pcm = toInt16(resampleTo16k(float32, audioContext.sampleRate));
    const level = rms(pcm);
    pushPreroll(pcm);

    // Turn timing is decided locally from the level — not from transcripts and
    // not with a server round-trip.
    const turn = ensureTurnTaker();
    turn.level(level);
    if (turn.shouldSend(level)) {
      S.socket.send(pcm.buffer);
      markPrerollSent();
    }
  }

  function duckBotAudio() {
    if (botAudio) botAudio.volume = 0.22;
  }

  function unduckBotAudio() {
    if (botAudio) botAudio.volume = 1;
  }

  function stopBotAudio() {
    audioQueue = [];  // never keep playing queued sentence frames after a stop
    if (botAudio) {
      botAudio.pause();
      botAudio = null;
      // Tell the server playback actually stopped: bot_speaking gates
      // turn-taking on the server side.
      wsSend({ type: 'playback_end' });
    }
    stopLevelMeter();
    if (mascot) mascot.setSpeaking(false);
    if (turnTaker) turnTaker.agentOutputEnd();
    stopOverlays();
  }

  function onBargeIn() {
    // Duck immediately, decide after the transcript: an "mm-hmm" must not kill
    // the reply, a real interruption must.
    haptic('light');
    duckBotAudio();
    stopOverlays();
    S.busy = false;
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
      micButton.classList.add('is-active');
      ensureTurnTaker().start();
      setVoiceState('listening', 'Listening');
      if (tg && tg.enableClosingConfirmation) tg.enableClosingConfirmation();
      haptic('medium');
      await ensureSocket();
    } catch (err) {
      toast(`Microphone unavailable: ${err.message || err}`);
      addMessage('error', `Microphone unavailable: ${err.message || err}`);
      await stopVoice();
    }
  }

  async function stopVoice() {
    S.voiceActive = false;
    S.busy = false;
    if (turnTaker) turnTaker.stop();
    micButton.classList.remove('is-active');
    hideActivity();
    if (workletNode) { workletNode.port.onmessage = null; workletNode.disconnect(); workletNode = null; }
    if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
    if (audioContext) { await audioContext.close(); audioContext = null; analyser = null; }
    if (botAudio) { botAudio.pause(); botAudio = null; }
    stopLevelMeter();
    if (mascot) mascot.setSpeaking(false);
    stopOverlays();
    if (tg && tg.disableClosingConfirmation) tg.disableClosingConfirmation();
  }

  // --------------------------------------------------------- mascot control
  // The mascot IS the interface: tap to start/stop talking, long-press to
  // open settings. The composer stays in the DOM (hidden) so text handling
  // keeps working, but there is deliberately no visible chrome.
  const mascotFigure = $('mascotFigure');
  let pressTimer = null;
  let pressStart = 0;

  function cancelLongPress() {
    if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
  }

  if (mascotFigure) {
    mascotFigure.addEventListener('pointerdown', (event) => {
      if (event.button && event.button !== 0) return;
      pressStart = Date.now();
      cancelLongPress();
      pressTimer = setTimeout(() => {
        pressTimer = null;
        haptic('heavy');
        openSheet();
      }, 600);
    });
    mascotFigure.addEventListener('pointerup', () => {
      const held = Date.now() - pressStart;
      const wasLongPress = pressTimer === null;
      cancelLongPress();
      if (!wasLongPress && held < 600) {
        haptic('light');
        micButton.click();
      }
    });
    mascotFigure.addEventListener('pointercancel', cancelLongPress);
    mascotFigure.addEventListener('pointerleave', cancelLongPress);
    mascotFigure.addEventListener('contextmenu', (event) => event.preventDefault());
  }

  // --------------------------------------------------------------- controls
  micButton.addEventListener('click', async () => {
    if (S.voiceActive) await stopVoice();
    else await startVoice();
  });

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
    toast(S.mode === 'echo' ? 'Echo test: replies repeat your words' : 'Agent mode: normal conversation');
  });

  moodSelect.addEventListener('change', () => {
    S.mood = moodSelect.value;
    wsSend({ type: 'set', mood: S.mood });
  });

  toggleSpeak.addEventListener('change', () => {
    S.speakReplies = toggleSpeak.checked;
    wsSend({ type: 'set', speak_text_replies: S.speakReplies });
  });

  toggleBackchannel.addEventListener('change', () => { S.backchannels = toggleBackchannel.checked; });

  toggleHalfDuplex.addEventListener('change', () => {
    S.duplex = toggleHalfDuplex.checked ? 'half' : 'full';
    if (turnTaker) {
      turnTaker.updateConfig({ halfDuplex: { preventWhileAgentSpeaking: S.duplex === 'half' } });
    }
    toast(S.duplex === 'half' ? 'Mic muted while OYI speaks' : 'Full-duplex barge-in on');
  });

  btnClear.addEventListener('click', () => {
    feed.innerHTML = '';
    // A fresh checkpointer thread too: stale history (e.g. echo-test turns,
    // half-finished barge-ins) must not color the next conversation.
    S.threadId = `${S.threadId}-${Date.now().toString(36)}`;
    wsSend({ type: 'set', thread_id: S.threadId });
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
  ensureSocket(); // preconnect so the first tap starts talking instantly
  checkHealth();
  window.addEventListener('beforeunload', () => { S.wantSocket = false; });
})();
