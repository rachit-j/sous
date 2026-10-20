class SousPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.samples = [];
    this.chunkSize = 512;
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    const output = outputs[0]?.[0];
    if (output) output.fill(0);
    if (!input?.length || !Number.isFinite(sampleRate) || sampleRate < 16000) return true;

    for (let index = 0; index < input.length; index += 1) {
      const sample = Number.isFinite(input[index]) ? input[index] : 0;
      this.sum += sample;
      this.count += 1;
      this.phase += 16000;
      if (this.phase >= sampleRate) {
        const resampled = this.sum / Math.max(1, this.count);
        this.samples.push(Math.max(-32768, Math.min(32767, Math.round(resampled * 32767))));
        this.phase -= sampleRate;
        this.sum = 0;
        this.count = 0;
      }
      if (this.samples.length === this.chunkSize) {
        const chunk = new Int16Array(this.samples);
        this.port.postMessage(chunk.buffer, [chunk.buffer]);
        this.samples.length = 0;
      }
    }
    return true;
  }
}

registerProcessor("sous-pcm-capture", SousPcmCapture);
