#!/usr/bin/env node
/* Tests for the local turn-taking state machine (webapp/turn-taking.js).
 *
 * Run:
 *     node test_turn_taking.js
 */

'use strict';

global.window = {};
require('./webapp/turn-taking.js');
const createTurnTaker = global.window.createTurnTaker;

const SPEECH = 0.06;
const QUIET = 0.002;

let failures = 0;
const tests = [];

function test(name, fn) {
  tests.push({ name, fn });
}

function harness(overrides) {
  let clock = 0;
  const events = [];
  const tt = createTurnTaker({
    now: () => clock,
    config: overrides || {},
    onEvent: (e) => events.push(e),
  });
  tt.start();
  return {
    tt,
    events,
    advance(ms) { clock += ms; },
    now() { return clock; },
    types() { return events.map((e) => e.type); },
  };
}

function feed(h, level, ms, step) {
  const interval = step || 20;
  for (let elapsed = 0; elapsed < ms; elapsed += interval) {
    h.advance(interval);
    h.tt.level(level);
  }
}

test('silence never opens a turn', () => {
  const h = harness();
  feed(h, QUIET, 600);
  if (h.types().includes('user_speech.started')) throw new Error('silence opened a turn');
  if (h.tt.state !== 'armed') throw new Error(`state ${h.tt.state}`);
});

test('sustained speech starts and silence ends the turn', () => {
  const h = harness();
  feed(h, SPEECH, 240);
  if (!h.types().includes('user_speech.started')) throw new Error('no speech_start');
  if (h.tt.state !== 'user_speaking') throw new Error(`state ${h.tt.state}`);
  feed(h, QUIET, 600);
  const ended = h.events.find((e) => e.type === 'user_speech.ended');
  if (!ended) throw new Error('no speech_end');
  if (ended.durationMs < 200) throw new Error(`duration ${ended.durationMs}`);
});

test('a short blip is not a turn', () => {
  const h = harness();
  feed(h, SPEECH, 100);
  feed(h, QUIET, 300);
  if (h.types().includes('user_speech.started')) throw new Error('blip became a turn');
});

test('barge-in needs sustained speech and ducks first', () => {
  const h = harness();
  h.tt.agentOutputStart();
  feed(h, SPEECH, 200); // under the 220ms qualification, and inside echo guard
  if (h.types().includes('barge_in.requested')) throw new Error('barged too early');
  h.advance(400); // past the echo guard
  feed(h, SPEECH, 240);
  if (!h.types().includes('barge_in.requested')) throw new Error('no barge_in');
  if (!h.types().includes('output.duck_requested')) throw new Error('did not duck');
  if (h.types().includes('output.cancel_requested')) throw new Error('cancelled too early');
});

test('echo is gated right after output starts', () => {
  const h = harness();
  h.tt.agentOutputStart();
  feed(h, SPEECH, 300); // all inside echoGuardMs (350)
  if (h.types().includes('barge_in.requested')) throw new Error('echo caused a barge');
  if (!h.types().includes('input.noise_gated')) throw new Error('echo was not gated');
});

test('sustained speech cancels output', () => {
  const h = harness();
  h.tt.agentOutputStart();
  h.advance(400);
  feed(h, SPEECH, 1000);
  if (!h.types().includes('output.cancel_requested')) throw new Error('did not cancel');
  if (h.tt.state !== 'interrupted') throw new Error(`state ${h.tt.state}`);
});

test('output ending mid-barge keeps the user turn open', () => {
  const h = harness();
  h.tt.agentOutputStart();
  h.advance(400);
  feed(h, SPEECH, 300);
  h.tt.agentOutputEnd();
  if (h.tt.state !== 'user_speaking') throw new Error(`state ${h.tt.state}`);
  feed(h, QUIET, 600);
  if (!h.types().includes('user_speech.ended')) throw new Error('turn never closed');
});

test('half-duplex holds the mic while the agent speaks', () => {
  const h = harness({ halfDuplex: { preventWhileAgentSpeaking: true } });
  h.tt.agentOutputStart();
  h.advance(400);
  feed(h, SPEECH, 800);
  if (h.types().includes('barge_in.requested')) throw new Error('half-duplex barged in');
  if (!h.types().includes('input.duplex_hold')) throw new Error('no duplex hold');
});

test('mutual exclusion: muted input is ignored', () => {
  const h = harness();
  h.tt.setMuted(true);
  feed(h, SPEECH, 800);
  if (h.types().includes('user_speech.started')) throw new Error('muted input opened a turn');
});

test('audio is only sent while a turn is open', () => {
  const h = harness();
  if (h.tt.shouldSend()) throw new Error('sends while idle');
  h.advance(10);
  h.tt.level(SPEECH);
  feed(h, SPEECH, 240);
  if (!h.tt.shouldSend()) throw new Error('does not send during a turn');
  feed(h, QUIET, 600);
  if (h.tt.shouldSend()) throw new Error('still sends after the turn');
});

for (const { name, fn } of tests) {
  try {
    fn();
    console.log(`\x1b[92m✓ ${name}\x1b[0m`);
  } catch (err) {
    failures += 1;
    console.log(`\x1b[91m✗ ${name}: ${err.message}\x1b[0m`);
  }
}
console.log(`\n${tests.length - failures}/${tests.length} passed`);
process.exit(failures ? 1 : 0);
