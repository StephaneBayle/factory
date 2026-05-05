class PCMForwarder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.bufferSize = 2048;
    this.floatBuffer = new Float32Array(this.bufferSize);
    this.offset = 0;
    this.phase = 0;

    this.port.onmessage = (event) => {
      if (event.data?.type === "config" && event.data.targetSampleRate) {
        this.targetSampleRate = event.data.targetSampleRate;
      }
    };
  }

  flushPCM() {
    if (this.offset === 0) {
      return;
    }
    const pcm = new Int16Array(this.offset);
    for (let i = 0; i < this.offset; i += 1) {
      const sample = Math.max(-1, Math.min(1, this.floatBuffer[i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    this.offset = 0;
  }

  pushSample(sample) {
    this.floatBuffer[this.offset] = sample;
    this.offset += 1;
    if (this.offset >= this.bufferSize) {
      this.flushPCM();
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) {
      return true;
    }

    const channel = input[0];
    const ratio = sampleRate / this.targetSampleRate;
    let peak = 0;

    for (let i = 0; i < channel.length; i += 1) {
      const sample = channel[i];
      const abs = Math.abs(sample);
      if (abs > peak) {
        peak = abs;
      }

      this.phase += 1;
      if (this.phase >= ratio) {
        this.phase -= ratio;
        this.pushSample(sample);
      }
    }

    this.port.postMessage({ type: "meter", peak });
    return true;
  }
}

registerProcessor("pcm-forwarder", PCMForwarder);
