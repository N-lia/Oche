/* OYI Live — the mascot.
 *
 * One purple blob on a white background. It breathes, blinks, looks around on
 * its own, and moves its mouth while the agent is speaking (driven by the
 * real output level when the browser gives us one, by a synthetic envelope
 * otherwise). Pure DOM/SVG: no assets, no network, no dependencies.
 *
 * Usage:
 *   const mascot = window.createMascot(document.getElementById('mascot'));
 *   mascot.setState('listening');
 *   mascot.setSpeaking(true);
 *   mascot.setLevel(0.42);
 */
(function () {
  'use strict';

  const BLINK_MIN_MS = 2400;
  const BLINK_MAX_MS = 7200;
  const GLANCE_MIN_MS = 1100;
  const GLANCE_MAX_MS = 3400;

  const PUPIL_RANGE_X = 7;
  const PUPIL_RANGE_Y = 4.5;

  function rand(min, max) {
    return min + Math.random() * (max - min);
  }

  function pick(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
  }

  function createMascot(root) {
    if (!root) return null;

    const byId = (id) => root.querySelector('#' + id);
    const face = byId('mascotFace');
    const eyeL = byId('eyeL');
    const eyeR = byId('eyeR');
    const pupilL = byId('pupilL');
    const pupilR = byId('pupilR');
    const browL = byId('browL');
    const browR = byId('browR');
    const mouth = byId('mouth');
    const mouthOpen = byId('mouthOpen');

    if (!face || !eyeL || !eyeR || !pupilL || !pupilR || !mouth || !mouthOpen) {
      return null;
    }

    const S = {
      mode: 'idle',
      speaking: false,
      level: 0,
      envelope: 0,
      raf: 0,
      lastFrame: 0,
      blinkTimer: 0,
      glanceTimer: 0,
      pupil: { x: 0, y: 0 },
      pupilTarget: { x: 0, y: 0 },
      appliedPupil: { x: null, y: null },
      blinkUntil: 0,
      pulseUntil: 0,
    };

    // ------------------------------------------------------------- rendering
    function applyPupils() {
      const x = S.pupil.x;
      const y = S.pupil.y;
      if (x === S.appliedPupil.x && y === S.appliedPupil.y) return;
      S.appliedPupil.x = x;
      S.appliedPupil.y = y;
      // CSS transforms need units; the SVG transform attribute would not.
      const transform = `translate(${x.toFixed(2)}px, ${y.toFixed(2)}px)`;
      pupilL.style.transform = transform;
      pupilR.style.transform = transform;
    }

    function applyBrows() {
      let lift = 0;
      let tilt = 0;
      if (S.mode === 'thinking') {
        lift = -6;
        tilt = -3;
      } else if (S.mode === 'listening') {
        lift = -2;
        tilt = -2;
      } else if (S.mode === 'speaking') {
        lift = -3;
        tilt = 0;
      }
      browL.style.transform = `translate(${tilt}px, ${lift}px) rotate(${tilt * 0.4}deg)`;
      browR.style.transform = `translate(${-tilt}px, ${lift}px) rotate(${-tilt * 0.4}deg)`;
    }

    function applyMouth(open) {
      mouthOpen.setAttribute('ry', Math.max(0.6, open).toFixed(2));
      const squiggle = S.speaking ? Math.max(0.08, 0.95 - open / 12) : 1;
      mouth.style.opacity = squiggle.toFixed(2);
    }

    function applyPulse() {
      const active = Date.now() < S.pulseUntil;
      root.classList.toggle('is-pulsing', active);
    }

    // ----------------------------------------------------------------- blink
    function blink() {
      const closed = 'scaleY(0.08)';
      eyeL.style.transform = closed;
      eyeR.style.transform = closed;
      setTimeout(() => {
        eyeL.style.transform = '';
        eyeR.style.transform = '';
      }, 95);
      S.blinkTimer = window.setTimeout(blink, rand(BLINK_MIN_MS, BLINK_MAX_MS));
    }

    // ---------------------------------------------------------------- glance
    function glance() {
      if (S.mode === 'thinking') {
        S.pupilTarget.x = rand(-PUPIL_RANGE_X, PUPIL_RANGE_X);
        S.pupilTarget.y = rand(-1.5, PUPIL_RANGE_Y);
      } else if (S.mode === 'listening') {
        S.pupilTarget.x = rand(-PUPIL_RANGE_X * 0.5, PUPIL_RANGE_X * 0.5);
        S.pupilTarget.y = rand(0, PUPIL_RANGE_Y);
      } else {
        S.pupilTarget.x = rand(-PUPIL_RANGE_X, PUPIL_RANGE_X);
        S.pupilTarget.y = rand(-PUPIL_RANGE_Y, PUPIL_RANGE_Y);
      }
      S.glanceTimer = window.setTimeout(glance, rand(GLANCE_MIN_MS, GLANCE_MAX_MS));
    }

    // -------------------------------------------------------------- mainloop
    function frame(now) {
      const dt = S.lastFrame ? Math.min(0.05, (now - S.lastFrame) / 1000) : 0.016;
      S.lastFrame = now;

      // Ease the pupils towards their target.
      const k = Math.min(1, dt * 7);
      S.pupil.x += (S.pupilTarget.x - S.pupil.x) * k;
      S.pupil.y += (S.pupilTarget.y - S.pupil.y) * k;

      if (S.mode === 'thinking') {
        // Pondering: eyes drift side to side, never settling.
        const drift = Math.sin(now / 620) * PUPIL_RANGE_X * 0.9;
        S.pupil.x = drift;
        S.pupil.y = Math.sin(now / 900) * 2;
      }
      applyPupils();

      if (S.speaking) {
        // Prefer the real output level; fall back to a lively envelope so the
        // mouth never freezes when the browser blocks the analyser.
        const wobble =
          0.5 +
          0.5 * Math.sin(now / 88) * Math.sin(now / 41 + 1.1) * Math.sin(now / 137 + 0.4);
        const target = Math.max(S.level * 2.6, wobble * 0.62);
        S.envelope += (target - S.envelope) * Math.min(1, dt * 16);
        applyMouth(1.6 + S.envelope * 8.5);
      } else {
        S.envelope *= 0.85;
        applyMouth(0.6);
      }

      applyPulse();
      S.raf = window.requestAnimationFrame(frame);
    }

    // ------------------------------------------------------------------ api
    function setState(mode) {
      const next = mode || 'idle';
      if (S.mode === next) return;
      S.mode = next;
      root.dataset.state = next;
      applyBrows();
    }

    function setSpeaking(on) {
      const next = Boolean(on);
      if (S.speaking === next) return;
      S.speaking = next;
      root.dataset.speaking = next ? 'true' : 'false';
      if (!next) {
        S.envelope = 0;
        applyMouth(0.6);
        mouth.style.opacity = '1';
      }
    }

    function setLevel(level) {
      const value = Number(level);
      S.level = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
    }

    function pulse() {
      S.pulseUntil = Date.now() + 420;
      applyPulse();
    }

    // ----------------------------------------------------------------- boot
    applyBrows();
    applyMouth(0.6);
    applyPupils();
    S.blinkTimer = window.setTimeout(blink, rand(600, 1400));
    S.glanceTimer = window.setTimeout(glance, rand(800, 2000));
    S.raf = window.requestAnimationFrame(frame);

    return {
      setState,
      setSpeaking,
      setLevel,
      pulse,
      destroy() {
        window.clearTimeout(S.blinkTimer);
        window.clearTimeout(S.glanceTimer);
        window.cancelAnimationFrame(S.raf);
      },
    };
  }

  window.createMascot = createMascot;
})();
