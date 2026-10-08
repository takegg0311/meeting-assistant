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
    // 許可ダイアログの待機より前に、クリックの中でAudioContextを起動する。
    const context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    this.audioContext = context;
    const running = context.resume();
    // 許可待ちの間にresumeが失敗しても未処理のPromiseにしない。
    const startup = await Promise.allSettled([provider.start(), running]);
    const failure = startup.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
    const stream = (startup[0] as PromiseFulfilledResult<MediaStream>).value;
    if (context.state !== "running") throw new Error("音声処理を開始できませんでした。もう一度セッションを開始してください。");
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
