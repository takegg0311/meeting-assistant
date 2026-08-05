import { useRef, useState } from "react";
import "./App.css";
import { AudioCapturePipeline } from "./audio/AudioCapturePipeline";
import type { AudioSourceProvider } from "./audio/AudioSourceProvider";
import { DisplayAudioSource, MicrophoneSource } from "./audio/AudioSourceProvider";
import { AudioSourceSelector } from "./components/AudioSourceSelector";
import { SttProviderSelector } from "./components/SttProviderSelector";
import { TranscriptPanel } from "./components/TranscriptPanel";
import type { AudioSource, SttProviderName, TranscriptEvent } from "./types/messages";
import { MeetingSocket } from "./ws/MeetingSocket";

const WS_URL = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws";

type SessionState = "idle" | "starting" | "active" | "stopping";

function App() {
  const [audioSource, setAudioSource] = useState<AudioSource>("microphone");
  const [sttProvider, setSttProvider] = useState<SttProviderName>("mock");
  const [sessionState, setSessionState] = useState<SessionState>("idle");
  const [segments, setSegments] = useState<TranscriptEvent[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const socketRef = useRef<MeetingSocket | null>(null);
  const pipelineRef = useRef<AudioCapturePipeline | null>(null);
  const providerRef = useRef<AudioSourceProvider | null>(null);

  const handleStart = async () => {
    setErrorMessage(null);
    setSessionState("starting");
    setSegments([]);

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

      {errorMessage && <p className="error-banner">{errorMessage}</p>}

      <main>
        <TranscriptPanel segments={segments} />
      </main>
    </div>
  );
}

export default App;
