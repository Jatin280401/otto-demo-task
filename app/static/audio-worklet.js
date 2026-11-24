class OttoCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._in = [];
    this._pos = 0;
    this._out = [];
    this._target = 16000;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const channel = input[0];
    for (let i = 0; i < channel.length; i++) this._in.push(channel[i]);

    const ratio = sampleRate / this._target;
    while (this._pos + 1 < this._in.length) {
      const i = Math.floor(this._pos);
      const frac = this._pos - i;
      const sample = this._in[i] * (1 - frac) + this._in[i + 1] * frac;
      this._out.push(sample);
      this._pos += ratio;
    }

    
    const consumed = Math.floor(this._pos);
    if (consumed > 4096) {
      this._in.splice(0, consumed);
      this._pos -= consumed;
    }

    while (this._out.length >= 1600) {
      const frame = new Float32Array(this._out.splice(0, 1600));
      this.port.postMessage(frame, [frame.buffer]);
    }
    return true;
  }
}

registerProcessor("otto-capture", OttoCaptureProcessor);
