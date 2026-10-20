/* BargeKit-style local turn-taking for the OYI Live WebApp.
 *
 * Timing decisions belong on the client: a server round-trip is too slow to
 * know when a turn starts, and a transcript cannot tell "mm-hmm" from "yes".
 * This is a small, deterministic state machine driven by microphone level:
 *
 *   armed -> user_speaking -> cooldown -> armed
 *   agent_speaking -> barge_pending -> (interrupted | agent_speaking)
 *
 * Echo policy: while the agent speaks, mic input is ignored for a short guard
 * window and barge-in needs sustained speech. We duck immediately and only
 * cancel on sustained speech; the server decides on the transcript whether a
 * short utterance was a continuer (resume) or a real turn (cancel).
 *
 * Local-only: nothing here sends audio anywhere.
 */
(function () {
  'use strict';

  const DEFAULTS = {
    mode: 'vad',
    speechThreshold: 0.022,
    noiseFloorThreshold: 0.008,
    minSpeechMs: 220,
    silenceMs: 750,
    debounceMs: 100,
    cooldownMs: 180,
    // Ignore the first moments of agent playback: speaker -> mic echo.
    echoGuardMs: 350,
    // Sustained speech beyond this is a real interruption, not an "mm-hmm".
    cancelAfterMs: 900,
    bargeIn: {
      enabled: true,
      whileAgentSpeaking: true,
      duckOutput: true,
      cancelOutput: true,
    },
    halfDuplex: {
      preventWhileAgentSpeaking: false,
    },
  };

  function isObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
  }

  function merge(base, patch) {
    const out = Object.assign({}, base);
    for (const key of Object.keys(patch || {})) {
      out[key] = isObject(base[key]) && isObject(patch[key])
        ? merge(base[key], patch[key])
        : patch[key];
    }
    return out;
  }

  function createTurnTaker(options) {
    const opts = options || {};
    let config = merge(DEFAULTS, opts.config || {});
    const notify = opts.onEvent || function () {};
    const clock = opts.now || function () { return performance.now(); };

    let state = 'armed';
    let muted = false;
    let running = false;
    let agentSpeaking = false;
    let agentStartedAt = 0;
    let speechStartedAt = 0;
    let silenceSince = 0;
    let cooldownUntil = 0;
    let bargeEmitted = false;
    let cancelEmitted = false;
    let noiseEmitted = false;

    function emit(type, extra) {
      try {
        notify(Object.assign({ type: type, state: state, at: Date.now() }, extra || {}));
      } catch (err) {
        // A UI handler must never break turn-taking.
        console.error('turn-taking handler failed', err);
      }
    }

    function setState(next, reason) {
      if (next === state) return;
      state = next;
      emit('state.changed', { next: next, reason: reason || '' });
    }

    function resetSpeech() {
      speechStartedAt = 0;
      silenceSince = 0;
      bargeEmitted = false;
      cancelEmitted = false;
    }

    function level(value, at) {
      if (!running || muted) return;
      const now = typeof at === 'number' ? at : clock();
      if (now < cooldownUntil) return;
      const speaking = value >= config.speechThreshold;

      if (agentSpeaking) {
        if (config.halfDuplex.preventWhileAgentSpeaking) {
          if (value >= config.noiseFloorThreshold) emit('input.duplex_hold', { level: value });
          return;
        }
        if (!config.bargeIn.enabled || !config.bargeIn.whileAgentSpeaking) {
          if (value >= config.noiseFloorThreshold) {
            emit('input.noise_gated', { reason: 'barge_in_disabled' });
          }
          return;
        }
        if (now - agentStartedAt < config.echoGuardMs) {
          if (value >= config.noiseFloorThreshold) {
            emit('input.noise_gated', { reason: 'echo_guard' });
          }
          return;
        }
        if (!speaking) {
          if (state === 'barge_pending') {
            // The "barge-in" lasted less than a syllable: close the turn we
            // already opened on the server so a ducked reply can resume.
            const durationMs = Math.round(now - speechStartedAt);
            resetSpeech();
            setState('agent_speaking', 'barge_aborted');
            emit('user_speech.ended', { durationMs: durationMs, aborted: true });
          }
          return;
        }
        if (!speechStartedAt) {
          speechStartedAt = now;
          setState('barge_pending', 'speech_during_output');
        }
        const sustained = now - speechStartedAt;
        const qualifyAt = Math.max(config.minSpeechMs, config.debounceMs);
        if (!bargeEmitted && sustained >= qualifyAt) {
          bargeEmitted = true;
          emit('barge_in.requested', { sustainedMs: Math.round(sustained) });
          if (config.bargeIn.duckOutput) emit('output.duck_requested', {});
        }
        if (!cancelEmitted && config.bargeIn.cancelOutput && sustained >= config.cancelAfterMs) {
          cancelEmitted = true;
          setState('interrupted', 'sustained_speech');
          emit('output.cancel_requested', { sustainedMs: Math.round(sustained) });
        }
        return;
      }

      if (state === 'user_speaking') {
        if (speaking) {
          silenceSince = 0;
          return;
        }
        if (!silenceSince) silenceSince = now;
        if (now - silenceSince >= config.silenceMs) {
          const durationMs = Math.round(now - speechStartedAt);
          resetSpeech();
          setState('cooldown', 'speech_end');
          emit('user_speech.ended', { durationMs: durationMs });
          cooldownUntil = now + config.cooldownMs;
          setTimeout(function () {
            if (state === 'cooldown') setState('armed', 'cooldown_elapsed');
          }, config.cooldownMs);
        }
        return;
      }

      if (speaking) {
        noiseEmitted = false;
        if (!speechStartedAt) speechStartedAt = now;
        if (now - speechStartedAt >= config.minSpeechMs) {
          silenceSince = 0;
          setState('user_speaking', 'speech_start');
          emit('user_speech.started', {});
        }
      } else if (value >= config.noiseFloorThreshold) {
        // Below speech but above the floor: noise/echo, not a turn.
        if (!noiseEmitted) {
          noiseEmitted = true;
          emit('input.noise_gated', { level: value });
        }
      } else {
        noiseEmitted = false;
      }
    }

    function agentOutputStart() {
      agentSpeaking = true;
      agentStartedAt = clock();
      resetSpeech();
      if (!muted) setState('agent_speaking', 'output_start');
    }

    function agentOutputEnd() {
      const wasBarging = state === 'barge_pending' || state === 'interrupted';
      agentSpeaking = false;
      resetSpeech();
      if (wasBarging) {
        // The user is mid-utterance; keep the turn open until silence ends it.
        speechStartedAt = clock();
        setState('user_speaking', 'output_end');
        return;
      }
      if (state === 'agent_speaking') {
        setState(muted ? 'muted' : 'armed', 'output_end');
      }
    }

    function setMuted(on) {
      muted = Boolean(on);
      if (muted) {
        resetSpeech();
        setState('muted', 'mute_on');
        emit('input.muted', {});
      } else {
        setState(agentSpeaking ? 'agent_speaking' : 'armed', 'mute_off');
      }
    }

    function updateConfig(patch) {
      config = merge(config, patch || {});
      return config;
    }

    // Audio is only streamed while a turn is open. The preroll sent with
    // speech_start/barge_in covers the onset, and the server ignores audio
    // outside a turn so noise and speaker echo can never open one.
    function shouldSend() {
      if (agentSpeaking) {
        if (config.halfDuplex.preventWhileAgentSpeaking) return false;
        return state === 'barge_pending' || state === 'interrupted';
      }
      return state === 'user_speaking';
    }

    function start() {
      running = true;
      resetSpeech();
      setState('armed', 'session_start');
    }

    function stop() {
      running = false;
      resetSpeech();
      setState('idle', 'session_stop');
    }

    return {
      start: start,
      stop: stop,
      level: level,
      agentOutputStart: agentOutputStart,
      agentOutputEnd: agentOutputEnd,
      setMuted: setMuted,
      updateConfig: updateConfig,
      shouldSend: shouldSend,
      get state() { return state; },
      get config() { return config; },
    };
  }

  window.createTurnTaker = createTurnTaker;
})();
