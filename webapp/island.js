/* OYI Live — the Dynamic Island.
   A black pill at the top of the screen that shows what OYI is doing right
   now. It morphs between three sizes:

     rest      a small empty capsule (idle, nothing to say)
     compact   a wide pill: an icon on the left, a short label, a live
               accessory on the right (timer, sound bars, spinner, check)
     expanded  a card with a title and one line of detail, for things worth
               a second look: errors, notices, a tool's detail on tap

   app.js drives it with set(state, opts), alert(opts) and setLevel(v).
   States: idle, connecting, offline, listening, holding, thinking, tool,
   replying, speaking, done. */

(() => {
  'use strict';

  const ICONS = {
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>',
    micOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="2" x2="22" y1="2" y2="22"/><path d="M18.89 13.23A7 7 0 0 0 19 12v-2"/><path d="M5 10v2a7 7 0 0 0 12 5"/><path d="M15 9.34V5a3 3 0 0 0-5.68-1.33"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12"/><line x1="12" x2="12" y1="19" y2="22"/></svg>',
    tool: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    pen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="7" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="11" y2="17"/><line x1="12" x2="12.01" y1="7" y2="7"/></svg>',
    plug: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/></svg>',
  };

  const SPINNER = '<span class="island__spinner" aria-hidden="true"></span>';
  const ORB = '<span class="island__orb" aria-hidden="true"></span>';
  const DOT = '<span class="island__dot" aria-hidden="true"></span>';
  const BAR_GAIN = [0.55, 0.85, 1, 0.8, 0.6];
  const BARS = `<span class="island__bars" aria-hidden="true">${BAR_GAIN.map(() => '<i></i>').join('')}</span>`;

  // What each state looks like. lead/trail are 'timer', 'bars', 'spinner',
  // 'check', 'dot' or an icon name.
  const LOOKS = {
    idle: { size: 'rest' },
    connecting: { size: 'compact', tone: 'warn', lead: 'dot', title: 'Connecting…', trail: 'spinner' },
    offline: { size: 'compact', tone: 'bad', lead: 'plug', title: 'Offline', trail: '' },
    listening: { size: 'compact', tone: 'live', lead: 'mic', title: 'Listening', trail: 'bars' },
    holding: { size: 'compact', tone: 'muted', lead: 'micOff', title: 'Mic held', trail: '' },
    thinking: { size: 'compact', tone: 'accent', lead: 'spinner', title: 'Thinking…', trail: 'timer' },
    tool: { size: 'compact', tone: 'accent', lead: 'tool', title: 'Working…', trail: 'spinner' },
    replying: { size: 'compact', tone: 'accent', lead: 'pen', title: 'Replying…', trail: 'timer' },
    speaking: { size: 'compact', tone: 'accent', lead: 'orb', title: 'Speaking', trail: 'bars' },
    done: { size: 'compact', tone: 'ok', lead: 'check', title: 'Done', trail: 'elapsed' },
  };

  const BUSY = new Set(['thinking', 'tool', 'replying']);

  function markup(kind) {
    if (!kind) return '';
    if (kind === 'spinner') return SPINNER;
    if (kind === 'bars') return BARS;
    if (kind === 'orb') return ORB;
    if (kind === 'dot') return DOT;
    if (kind === 'timer' || kind === 'elapsed') return '<span class="island__time"></span>';
    return ICONS[kind] ? `<span class="island__icon">${ICONS[kind]}</span>` : '';
  }

  function seconds(ms) {
    return `${(Math.max(0, ms) / 1000).toFixed(1)}s`;
  }

  function createIsland(root, options = {}) {
    if (!root) return null;
    const lead = root.querySelector('.island__lead');
    const title = root.querySelector('.island__title');
    const detail = root.querySelector('.island__detail');
    const trail = root.querySelector('.island__trail');
    const haptic = options.haptic || (() => {});
    const now = options.now || (() => Date.now());

    const I = {
      state: 'idle',
      opts: {},
      busySince: 0,
      lastElapsed: 0,
      alert: null, // { kind, title, detail, until }
      alertTimer: 0,
      doneTimer: 0,
      tickTimer: 0,
      expandedByTap: false,
      level: 0,
      levelAt: 0,
      rafId: 0,
      rendered: '',
    };

    // ------------------------------------------------------------ render
    function render() {
      let size;
      let tone;
      let leadKind;
      let trailKind;
      let titleText;
      let detailText = '';

      if (I.alert) {
        size = 'expanded';
        tone = I.alert.kind === 'error' ? 'bad' : I.alert.kind === 'success' ? 'ok' : 'accent';
        leadKind = I.alert.kind === 'error' ? 'alert' : I.alert.kind === 'success' ? 'check' : 'info';
        trailKind = '';
        titleText = I.alert.title;
        detailText = I.alert.detail || '';
      } else {
        const look = LOOKS[I.state] || LOOKS.idle;
        size = look.size;
        tone = look.tone || '';
        leadKind = I.opts.lead || look.lead;
        trailKind = I.opts.trail != null ? I.opts.trail : look.trail;
        titleText = I.opts.title || look.title || '';
        detailText = I.opts.detail || '';
        if (I.expandedByTap && detailText) size = 'expanded';
      }

      root.dataset.size = size;
      root.dataset.tone = tone;
      root.dataset.state = I.alert ? 'alert' : I.state;
      root.setAttribute('aria-label', [titleText, detailText].filter(Boolean).join('. ') || 'OYI');

      // Only rebuild the accessories when they change, so spinners and bars
      // keep animating smoothly between updates.
      const key = `${leadKind}|${trailKind}`;
      if (key !== I.rendered) {
        I.rendered = key;
        lead.innerHTML = markup(leadKind);
        trail.innerHTML = markup(trailKind);
      }
      if (title.textContent !== titleText) title.textContent = titleText;
      if (detail.textContent !== detailText) detail.textContent = detailText;
      tickTime();
      syncLoops(trailKind);
    }

    function tickTime() {
      const time = trail.querySelector('.island__time');
      if (!time) return;
      if (I.state === 'done') time.textContent = I.lastElapsed ? seconds(I.lastElapsed) : '';
      else time.textContent = I.busySince ? seconds(now() - I.busySince) : '';
    }

    // Timers and the level animation run only while something shows them.
    function syncLoops(trailKind) {
      const wantsTick = !I.alert && (trailKind === 'timer');
      if (wantsTick && !I.tickTimer) I.tickTimer = setInterval(tickTime, 100);
      if (!wantsTick && I.tickTimer) { clearInterval(I.tickTimer); I.tickTimer = 0; }

      const wantsBars = !I.alert && trailKind === 'bars';
      if (wantsBars && !I.rafId) I.rafId = requestAnimationFrame(drawBars);
      if (!wantsBars && I.rafId) { cancelAnimationFrame(I.rafId); I.rafId = 0; }
    }

    function drawBars(t) {
      I.rafId = 0;
      const bars = trail.querySelectorAll('.island__bars i');
      if (!bars.length) return;
      // A real level when one arrives recently; otherwise a gentle synthetic
      // wave so speaking never looks frozen (some browsers block the meter).
      const fresh = now() - I.levelAt < 250;
      let base = fresh ? I.level : 0;
      if (!fresh && I.state === 'speaking') base = 0.35 + 0.25 * Math.sin(t / 170);
      bars.forEach((bar, i) => {
        const wobble = 0.12 * Math.sin(t / 90 + i * 1.7);
        const h = Math.max(0.18, Math.min(1, base * BAR_GAIN[i] * 1.6 + (base > 0.04 ? wobble : 0)));
        bar.style.transform = `scaleY(${h.toFixed(3)})`;
      });
      I.rafId = requestAnimationFrame(drawBars);
    }

    // ------------------------------------------------------------- API
    function set(state, opts = {}) {
      if (!LOOKS[state]) state = 'idle';
      clearTimeout(I.doneTimer);
      I.doneTimer = 0;

      const wasBusy = BUSY.has(I.state);
      const isBusy = BUSY.has(state);
      if (isBusy && !wasBusy) I.busySince = now();
      if (!isBusy && wasBusy) I.lastElapsed = I.busySince ? now() - I.busySince : 0;
      if (!isBusy) I.busySince = 0;

      if (state !== I.state) {
        I.expandedByTap = false;
        if (state === 'done') haptic('success');
      }
      I.state = state;
      I.opts = opts;
      render();

      if (state === 'done') {
        I.doneTimer = setTimeout(() => {
          I.doneTimer = 0;
          // `after` may be a function so the island settles on whatever is
          // true when "Done" ends, not when it began.
          const next = typeof opts.after === 'function' ? opts.after() : [opts.after || 'idle', opts.afterOpts || {}];
          set(next[0], next[1]);
        }, opts.holdMs || 1600);
      }
    }

    // A short-lived expanded card. The underlying state keeps updating
    // underneath and shows again when the alert ends.
    function alert({ kind = 'info', title: alertTitle = '', detail: alertDetail = '', ms = 3200 } = {}) {
      clearTimeout(I.alertTimer);
      I.alert = { kind, title: alertTitle, detail: alertDetail };
      if (kind === 'error') haptic('error');
      render();
      I.alertTimer = setTimeout(dismiss, ms);
    }

    function dismiss() {
      clearTimeout(I.alertTimer);
      I.alertTimer = 0;
      if (!I.alert) return;
      I.alert = null;
      render();
    }

    function setLevel(value) {
      I.level = Math.max(0, Math.min(1, Number(value) || 0));
      I.levelAt = now();
    }

    // Tap: close an alert, or open/close the detail of the current state.
    root.addEventListener('click', (event) => {
      event.stopPropagation();
      if (I.alert) { dismiss(); return; }
      if (I.opts.detail) {
        I.expandedByTap = !I.expandedByTap;
        haptic('light');
        render();
      }
    });

    render();
    return {
      set,
      alert,
      dismiss,
      setLevel,
      get state() { return I.state; },
      get alerting() { return Boolean(I.alert); },
    };
  }

  window.createIsland = createIsland;
})();
