export type AudioSource = "microphone" | "tab_audio" | "system_audio";
export type SttProviderName = "cloud" | "local" | "mock";

export interface StartSessionMessage {
  type: "start_session";
  stt_provider: SttProviderName;
  audio_source: AudioSource;
  features: string[];
}

export interface StopSessionMessage {
  type: "stop_session";
}

export interface TranscriptEvent {
  type: "transcript";
  segment_id: string;
  speaker_id: string | null;
  audio_source: AudioSource;
  text: string;
  is_final: boolean;
  start_ts: number;
  end_ts: number;
}

export interface StatusEvent {
  type: "status";
  stage: "stt_connected" | "processing" | "error" | "session_stopped";
  message: string;
}

export type ServerEvent = TranscriptEvent | StatusEvent;
