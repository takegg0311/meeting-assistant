import { useRef, useState } from "react";
import "./App.css";
import { AudioCapturePipeline } from "./audio/AudioCapturePipeline";
import type { AudioSourceProvider } from "./audio/AudioSourceProvider";
import { DisplayAudioSource, MicrophoneSource } from "./audio/AudioSourceProvider";
import { AnswerSuggestionPanel } from "./components/AnswerSuggestionPanel";
import { AudioSourceSelector } from "./components/AudioSourceSelector";
import { SttProviderSelector } from "./components/SttProviderSelector";
import { TranscriptPanel } from "./components/TranscriptPanel";
import type {
  AnswerSuggestionEvent,
  AudioSourceMode,
  SttProviderName,
  TranscriptEvent,
} from "./types/messages";
import { MeetingSocket } from "./ws/MeetingSocket";

// 接続先は vite.config.ts が BACKEND_PORT / VITE_WS_URL から解決してビルド時に注入する。
const WS_URL = import.meta.env.VITE_WS_URL;

type InputStatus = { label: string; level: number; chunks: number };

type SessionState = "idle" | "starting" | "active" | "stopping";

function App() {
  const [audioSource, setAudioSource] = useState<AudioSourceMode>("microphone");
  const [sttProvider, setSttProvider] = useState<SttProviderName>("mock");
  const [vadThreshold, setVadThreshold] = useState(-45);
  const [sessionState, setSessionState] = useState<SessionState>("idle");
  const [segments, setSegments] = useState<TranscriptEvent[]>([]);
  const [suggestions, setSuggestions] = useState<AnswerSuggestionEvent[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [inputStatus, setInputStatus] = useState<InputStatus[]>([]);

  const socketRef = useRef<MeetingSocket | null>(null);
  const pipelineRef = useRef<AudioCapturePipeline[]>([]);
  const providerRef = useRef<AudioSourceProvider[]>([]);

  /** 未完了の生成中カードをエラー表示に落とす。
   * セッションを閉じると結果は届かないため、生成中のまま残さない。 */
  const failPendingSuggestions = () => {
    setSuggestions((prev) =>
      prev.map((s) =>
        s.status === "generating"
          ? { ...s, status: "error", message: "セッションが終了したため中断しました。" }
          : s,
      ),
    );
  };

  const handleStart = async () => {
    setErrorMessage(null);
    setSessionState("starting");
    setSegments([]);
    setSuggestions([]);
    setInputStatus([]);

    const socket = new MeetingSocket(WS_URL);
    const providers: AudioSourceProvider[] = [];
    const pipelines: AudioCapturePipeline[] = [];
    let sending = false;
    try {
      // 全入力の取得・AudioContext開始を最初のawaitより前に呼び出す。
      providers.push(audioSource === "microphone" ? new MicrophoneSource() : new DisplayAudioSource());
      if (audioSource === "meeting") providers.push(new MicrophoneSource());
      const status = providers.map((_, index) => ({
        label: audioSource === "microphone" || index === 1 ? "マイク（自分）" : "共有音声（相手）",
        level: 0, chunks: 0,
      }));
      setInputStatus(status);
      let lastUpdate = 0;
      providers.forEach((_, index) => {
        pipelines.push(new AudioCapturePipeline((chunk) => {
          if (!sending) return;
          socket.sendAudioChunk(chunk, audioSource === "meeting" ? index : undefined);
          const pcm = new Int16Array(chunk);
          let energy = 0;
          for (const sample of pcm) energy += (sample / 32768) ** 2;
          status[index] = { ...status[index],
            level: Math.sqrt(energy / Math.max(1, pcm.length)),
            chunks: status[index].chunks + 1 };
          if (performance.now() - lastUpdate > 250) {
            lastUpdate = performance.now();
            setInputStatus(status.map((input) => ({ ...input })));
          }
        }));
      });
      const starts = await Promise.allSettled(pipelines.map((pipeline, index) => pipeline.start(providers[index])));
      const failed = starts.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
      await socket.connect();

      socket.onEvent((event) => {
        if (event.type === "transcript") {
          setSegments((prev) => {
            const existingIndex = prev.findIndex((s) => s.segment_id === event.segment_id);
            if (existingIndex === -1) {
              return [...prev, event].sort((a, b) => a.start_ts - b.start_ts);
            }
            const next = [...prev];
            next[existingIndex] = event;
            return next.sort((a, b) => a.start_ts - b.start_ts);
          });
        } else if (event.type === "answer_suggestion") {
          // generating で追加され、done|error で同じ request_id のカードを置き換える。
          setSuggestions((prev) => {
            const existingIndex = prev.findIndex((s) => s.request_id === event.request_id);
            if (existingIndex === -1) {
              return [...prev, event];
            }
            const next = [...prev];
            next[existingIndex] = event;
            return next;
          });
        } else if (event.type === "status" && event.stage === "error") {
          setErrorMessage(event.message);
        }
      });

      // 予期しない切断でも生成中カードを残さない(サーバー停止など)。
      socket.onClose(() => failPendingSuggestions());

      socket.startSession({
        stt_provider: sttProvider,
        audio_source: providers[0].sourceType,
        ...(audioSource === "meeting" ? { audio_sources: providers.map((p) => p.sourceType) } : {}),
        features: [],
        vad_threshold_dbfs: vadThreshold,
      });
      sending = true;
      socketRef.current = socket;
      pipelineRef.current = pipelines;
      providerRef.current = providers;
      setSessionState("active");
    } catch (err) {
      pipelines.forEach((pipeline) => pipeline.stop());
      providers.forEach((provider) => provider.stop());
      socket.close();
      setErrorMessage(err instanceof Error ? err.message : String(err));
      setSessionState("idle");
    }
  };

  const handleStop = async () => {
    setSessionState("stopping");
    pipelineRef.current.forEach((pipeline) => pipeline.stop());
    providerRef.current.forEach((provider) => provider.stop());

    socketRef.current?.stopSession();
    await socketRef.current?.waitForStop();
    socketRef.current?.close();

    socketRef.current = null;
    pipelineRef.current = [];
    providerRef.current = [];
    failPendingSuggestions();
    setSessionState("idle");
  };

  const handleRequestAnswerSuggestion = () => {
    // request_id で generating カードと結果を紐付ける。連打は並行して受け付ける。
    socketRef.current?.requestAnswerSuggestion(crypto.randomUUID());
  };

  const isSessionActive = sessionState === "active" || sessionState === "starting";

  return (
    <div className="app">
      <header>
        <h1>Meeting Assistant</h1>
      </header>

      <section className="controls">
        <AudioSourceSelector value={audioSource} disabled={isSessionActive} onChange={setAudioSource} />
        <SttProviderSelector value={sttProvider} disabled={isSessionActive} onChange={setSttProvider} />
        {sessionState === "idle" ? (
          <button onClick={handleStart}>セッション開始</button>
        ) : (
          <button onClick={handleStop} disabled={sessionState === "stopping" || sessionState === "starting"}>
            セッション終了
          </button>
        )}
      </section>

      <section className="controls" aria-label="STT共通の無音判定">
        <label htmlFor="vad-threshold">無音判定の音量：{vadThreshold} dBFS</label>
        <input id="vad-threshold" type="range" min={-70} max={-15} step={1}
          value={vadThreshold} disabled={sessionState !== "idle"}
          onChange={(event) => setVadThreshold(Number(event.target.value))} />
        <span className="answer-request-hint">
          この音量以下が0.8秒続くと発話を区切ります（各入力を個別に判定）。
          右ほど無音と判定しやすくなります。変更は開始前に行えます。
        </span>
      </section>

      <section className="controls">
        <button
          type="button"
          className="answer-request"
          onClick={handleRequestAnswerSuggestion}
          disabled={sessionState !== "active"}
        >
          回答提案
        </button>
        <span className="answer-request-hint">
          今の問いへの回答案を作ります(直前の発話が確定するまで少し待ちます)
        </span>
      </section>

      {sessionState === "active" && (
        <section className="controls" aria-label="音声入力状況">
          {inputStatus.map((input) => (
            <label key={input.label}>
              {input.label} <meter min={0} max={0.2} value={input.level} aria-label={`${input.label}の音量`} />
              {` ${input.level > 0 ? (20 * Math.log10(input.level)).toFixed(0) : "−∞"} dBFS`}
              {input.chunks === 0 ? " 音声データ待ち" : ` 送信中（${input.chunks}チャンク）`}
            </label>
          ))}
        </section>
      )}

      {errorMessage && <p className="error-banner">{errorMessage}</p>}

      {/* 広い画面では左に文字起こし、右に回答提案を横並べにし、狭い画面では縦積みにする。
          高さは1画面に収め、あふれた分は枠ごとにスクロールさせる(App.cssを参照)。 */}
      <main className="panes">
        <TranscriptPanel segments={segments} />
        <AnswerSuggestionPanel suggestions={suggestions} />
      </main>
    </div>
  );
}

export default App;
