// AudioWorkletProcessor: Float32 → PCM16(mono)に変換し、一定サンプル数ごとにメインスレッドへ転送する。
class PcmWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // 128 samples/callback * 8 ≈ 32ms分をまとめて送信(20〜100msの範囲内)
    this.targetFrameCount = 512;
    this.buffer = new Float32Array(0);
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;

    const channelData = input[0];
    if (!channelData) return true;

    const merged = new Float32Array(this.buffer.length + channelData.length);
    merged.set(this.buffer);
    merged.set(channelData, this.buffer.length);
    this.buffer = merged;

    while (this.buffer.length >= this.targetFrameCount) {
      const frame = this.buffer.slice(0, this.targetFrameCount);
      this.buffer = this.buffer.slice(this.targetFrameCount);
      this.port.postMessage(this._floatToPcm16(frame), [frame.buffer]);
    }

    return true;
  }

  _floatToPcm16(float32Array) {
    const pcm16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const clamped = Math.max(-1, Math.min(1, float32Array[i]));
      pcm16[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    }
    return pcm16.buffer;
  }
}

registerProcessor("pcm-worklet-processor", PcmWorkletProcessor);
