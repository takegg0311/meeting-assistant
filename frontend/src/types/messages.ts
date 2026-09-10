export type AudioSource = "microphone" | "tab_audio" | "system_audio";
export type SttProviderName = "cloud_openai" | "cloud_google" | "local_whispercpp" | "mock";

export interface StartSessionMessage {
  type: "start_session";
  stt_provider: SttProviderName;
  audio_source: AudioSource;
  features: string[];
}

export interface StopSessionMessage {
  type: "stop_session";
}

/** 「回答提案」ボタン押下。押下時点ではSTTが未確定な可能性が高いため、
 * サーバー側で確定セグメントを短時間待ってから生成される(grace period)。 */
export interface RequestAnswerSuggestionMessage {
  type: "request_answer_suggestion";
  request_id: string;
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

/** 回答提案の生成状況と結果。1リクエストにつき generating → done|error の順で届く。 */
export interface AnswerSuggestionEvent {
  type: "answer_suggestion";
  request_id: string;
  status: "generating" | "done" | "error";
  answer: string;
  source_segment_ids: string[];
  message: string;
}

export type ServerEvent = TranscriptEvent | StatusEvent | AnswerSuggestionEvent;
