import type { AudioSource } from "../types/messages";

export interface AudioSourceProvider {
  readonly sourceType: AudioSource;
  start(): Promise<MediaStream>;
  stop(): void;
}

export class MicrophoneSource implements AudioSourceProvider {
  readonly sourceType: AudioSource = "microphone";
  private stream: MediaStream | null = null;

  async start(): Promise<MediaStream> {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return this.stream;
  }

  stop(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
}

export class DisplayAudioSource implements AudioSourceProvider {
  // ピッカーで「タブ」を選ぶか「画面全体」を選ぶかは実行時にしか分からないため、
  // start() 完了後に確定した値で上書きされる。
  sourceType: AudioSource = "system_audio";
  private stream: MediaStream | null = null;

  async start(): Promise<MediaStream> {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: true, // 仕様上、音声のみの取得はできないため必須
      audio: { systemAudio: "include" } as MediaTrackConstraints,
    });
    stream.getVideoTracks().forEach((track) => track.stop()); // 映像は即座に破棄し音声のみ使用

    const [audioTrack] = stream.getAudioTracks();
    const displaySurface = (audioTrack?.getSettings() as { displaySurface?: string })?.displaySurface;
    this.sourceType = displaySurface === "browser" ? "tab_audio" : "system_audio";

    this.stream = stream;
    return stream;
  }

  stop(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
}

/** Firefox/Safariでは getDisplayMedia の音声取得が仕様上無視されるため、PC音声の選択肢を無効化する判定に使う。 */
export function isDisplayAudioSupported(): boolean {
  const ua = navigator.userAgent;
  const isFirefox = ua.includes("Firefox");
  const isSafari = ua.includes("Safari") && !ua.includes("Chrome") && !ua.includes("Edg");
  return !isFirefox && !isSafari;
}
