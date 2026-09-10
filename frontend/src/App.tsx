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
  AudioSource,
  SttProviderName,
  TranscriptEvent,
} from "./types/messages";
import { MeetingSocket } from "./ws/MeetingSocket";

// 接続先は vite.config.ts が BACKEND_PORT / VITE_WS_URL から解決してビルド時に注入する。
const WS_URL = import.meta.env.VITE_WS_URL;

type SessionState = "idle" | "starting" | "active" | "stopping";

function App() {
  const [audioSource, setAudioSource] = useState<AudioSource>("microphone");
  const [sttProvider, setSttProvider] = useState<SttProviderName>("mock");
  const [sessionState, setSessionState] = useState<SessionState>("idle");
  const [segments, setSegments] = useState<TranscriptEvent[]>([]);
  const [suggestions, setSuggestions] = useState<AnswerSuggestionEvent[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const socketRef = useRef<MeetingSocket | null>(null);
  const pipelineRef = useRef<AudioCapturePipeline | null>(null);
  const providerRef = useRef<AudioSourceProvider | null>(null);

  const handleStart = async () => {
    setErrorMessage(null);
    setSessionState("starting");
    setSegments([]);
    setSuggestions([]);

    try {
      const socket = new MeetingSocket(WS_URL);
      await socket.connect();

      socket.onEvent((event) => {
        if (event.type === "transcript") {
          setSegments((prev) => {
            const existingIndex = prev.findIndex((s) => s.segment_id === event.segment_id);
            if (existingIndex === -1) {
              return [...prev, event];
            }
            const next = [...prev];
            next[existingIndex] = event;
            return next;
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

      const provider: AudioSourceProvider =
        audioSource === "microphone" ? new MicrophoneSource() : new DisplayAudioSource();

      const pipeline = new AudioCapturePipeline((chunk) => {
        socket.sendAudioChunk(chunk);
      });
      await pipeline.start(provider);

      socket.startSession({
        stt_provider: sttProvider,
        audio_source: provider.sourceType,
        features: [],
      });

      socketRef.current = socket;
      pipelineRef.current = pipeline;
      providerRef.current = provider;
      setSessionState("active");
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
      setSessionState("idle");
    }
  };

  const handleStop = async () => {
    setSessionState("stopping");
    pipelineRef.current?.stop();
    providerRef.current?.stop();

    socketRef.current?.stopSession();
    await socketRef.current?.waitForStop();
    socketRef.current?.close();

    socketRef.current = null;
    pipelineRef.current = null;
    providerRef.current = null;
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
          <button onClick={handleStop} disabled={sessionState === "stopping"}>
            セッション終了
          </button>
        )}
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
