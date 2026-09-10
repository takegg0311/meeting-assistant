import type {
  RequestAnswerSuggestionMessage,
  ServerEvent,
  StartSessionMessage,
  StopSessionMessage,
} from "../types/messages";

type EventListener = (event: ServerEvent) => void;
type CloseListener = () => void;

/**
 * 音声チャンクの送信バッファ。接続が切れている間の送信要求を溜めておき、
 * 再接続後にシーケンス番号順で再送する。
 */
class SendBuffer {
  private pending: { seq: number; chunk: ArrayBuffer }[] = [];
  private nextSeq = 0;

  enqueue(chunk: ArrayBuffer): { seq: number; chunk: ArrayBuffer } {
    const item = { seq: this.nextSeq++, chunk };
    this.pending.push(item);
    return item;
  }

  acknowledge(uptoSeq: number): void {
    this.pending = this.pending.filter((item) => item.seq > uptoSeq);
  }

  get all(): { seq: number; chunk: ArrayBuffer }[] {
    return this.pending;
  }
}

export class MeetingSocket {
  private ws: WebSocket | null = null;
  private listeners: Set<EventListener> = new Set();
  private closeListeners: Set<CloseListener> = new Set();
  private sendBuffer = new SendBuffer();
  private readonly url: string;

  constructor(url: string) {
    this.url = url;
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);
      this.ws.binaryType = "arraybuffer";

      this.ws.onopen = () => {
        this._resendUnacked();
        resolve();
      };
      // WebSocketのonerrorはEventしか渡さないため、そのままrejectすると `[object Event]` と
      // 表示され原因が分からない。接続先を含むErrorに変換する。
      this.ws.onerror = () =>
        reject(
          new Error(
            `WebSocketの接続に失敗しました: ${this.url} (バックエンドが起動しているか、ポート番号が一致しているか確認してください)`,
          ),
        );
      this.ws.onmessage = (event) => this._handleMessage(event);
      this.ws.onclose = () => {
        // 音声送信は継続してバッファに溜め、再接続時にresendする想定。
        // 生成中の回答提案は結果が届かなくなるため、購読者へ通知する。
        this.closeListeners.forEach((listener) => listener());
      };
    });
  }

  onEvent(listener: EventListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** 接続が閉じたときに呼ばれる。意図的な close と予期しない切断の両方で発火する。 */
  onClose(listener: CloseListener): () => void {
    this.closeListeners.add(listener);
    return () => this.closeListeners.delete(listener);
  }

  startSession(message: Omit<StartSessionMessage, "type">): void {
    this._sendControl({ type: "start_session", ...message });
  }

  stopSession(): void {
    this._sendControl<StopSessionMessage>({ type: "stop_session" });
  }

  /** 回答提案をリクエストする。結果は answer_suggestion イベントで request_id 紐付けで届く。 */
  requestAnswerSuggestion(requestId: string): void {
    this._sendControl<RequestAnswerSuggestionMessage>({
      type: "request_answer_suggestion",
      request_id: requestId,
    });
  }

  /** stop_session送信後、サーバーのsession_stopped応答を待ってからclose()する。
   * 応答を待たずに閉じるとサーバー側の送信が失敗しエラーログが出るため。 */
  waitForStop(timeoutMs = 2000): Promise<void> {
    return new Promise((resolve) => {
      if (!this._isOpen()) {
        resolve();
        return;
      }

      const timer = setTimeout(() => {
        unsubscribe();
        resolve();
      }, timeoutMs);

      const unsubscribe = this.onEvent((event) => {
        if (event.type === "status" && event.stage === "session_stopped") {
          clearTimeout(timer);
          unsubscribe();
          resolve();
        }
      });
    });
  }

  sendAudioChunk(chunk: ArrayBuffer): void {
    const item = this.sendBuffer.enqueue(chunk);
    if (this._isOpen()) {
      this.ws!.send(item.chunk);
    }
  }

  close(): void {
    this.ws?.close();
    this.ws = null;
  }

  private _resendUnacked(): void {
    if (!this._isOpen()) return;
    for (const item of this.sendBuffer.all) {
      this.ws!.send(item.chunk);
    }
  }

  private _isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private _sendControl<T extends { type: string }>(message: T): void {
    if (!this._isOpen()) return;
    this.ws!.send(JSON.stringify(message));
  }

  private _handleMessage(event: MessageEvent): void {
    if (typeof event.data !== "string") return;
    const parsed = JSON.parse(event.data) as ServerEvent;
    this.listeners.forEach((listener) => listener(parsed));
  }
}
