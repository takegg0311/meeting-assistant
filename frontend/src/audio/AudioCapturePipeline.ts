import type { AudioSourceProvider } from "./AudioSourceProvider";

const TARGET_SAMPLE_RATE = 16000;

export class AudioCapturePipeline {
  private audioContext: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private readonly onPcmChunk: (chunk: ArrayBuffer) => void;

  constructor(onPcmChunk: (chunk: ArrayBuffer) => void) {
    this.onPcmChunk = onPcmChunk;
  }

  async start(provider: AudioSourceProvider): Promise<void> {
    const stream = await provider.start();

    this.audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    await this.audioContext.audioWorklet.addModule("/pcm-worklet.js");

    this.sourceNode = this.audioContext.createMediaStreamSource(stream);
    this.workletNode = new AudioWorkletNode(this.audioContext, "pcm-worklet-processor");
    this.workletNode.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      this.onPcmChunk(event.data);
    };

    this.sourceNode.connect(this.workletNode);
    // workletNodeの出力は使わないが、Chromeの一部バージョンではグラフ接続がないとprocess()が呼ばれないため
    // 無音のdestination接続で駆動させる。
    const silentGain = this.audioContext.createGain();
    silentGain.gain.value = 0;
    this.workletNode.connect(silentGain).connect(this.audioContext.destination);
  }

  stop(): void {
    this.workletNode?.disconnect();
    this.sourceNode?.disconnect();
    this.audioContext?.close();
    this.workletNode = null;
    this.sourceNode = null;
    this.audioContext = null;
  }
}
