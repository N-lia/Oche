// Captures microphone input and forwards fixed-size Float32 frames to the
// main thread. Resampling to 16 kHz and Int16 packing happen in app.js.
class MicWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = [];
    this._frameSize = 1024; // ~21ms at 48kHz
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channel = input[0];
    for (let i = 0; i < channel.length; i += 1) {
      this._buffer.push(channel[i]);
    }

    while (this._buffer.length >= this._frameSize) {
      const frame = new Float32Array(this._buffer.splice(0, this._frameSize));
      this.port.postMessage(frame, [frame.buffer]);
    }
    return true;
  }
}

registerProcessor('mic-worklet', MicWorklet);
