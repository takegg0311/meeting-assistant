# リアルタイム会議補助Webアプリケーション システム設計

## 1. 設計方針

要件の優先度(1: 文字起こし → 5: 話者判別)は、そのままレイテンシ要求の厳しさと対応する。

| 優先度 | 機能 | レイテンシ要求 | 実行方式 |
|---|---|---|---|
| 1 | 文字起こし | 数百ms〜1秒(体感リアルタイム) | STTストリーミング、ブロッキングなし |
| 2 | 話題のファクトチェック/情報収集 | 数秒(遅延許容) | 非同期タスク、fire-and-forget |
| 3 | 質問への回答提案 | 2〜3秒目標 | 非同期タスク、優先度高め(速いモデル) |
| 4 | 想定質問の生成 | 遅延許容(アイドル時でよい) | 非同期・バックグラウンド、低優先 |
| 5 | 話者判別 | 遅延許容、精度もベストエフォート | 非同期、単一マイクの制約を明示 |
| - | 議事録生成 | 会議終了後、数分の遅延OK | バッチジョブ |

**核心となる設計判断**: 文字起こし(優先度1)のパイプラインを他の処理から完全に分離し、絶対にブロックしない。優先度2〜5は「文字起こしイベントに反応して非同期に発火するタスク」として設計し、結果が出来次第WebSocketで画面に流し込む(結果が来ないうちはUIパネルは空/ローディング表示)。

## 2. アーキテクチャ概要

```
Client (Browser) [音声入力: マイク / PC音声(タブ・システム)を切替]
  └─ WebSocket ─→ FastAPI backend (WebSocketハブ / MeetingSessionオーケストレーター)
                     ├─ STT provider (Cloud / Local 切替可能)
                     │    └─ 確定transcriptイベントをSessionに供給
                     ├─ Analysis pipeline (非同期タスク群、それぞれ独立して発火)
                     │    ├─ Topic/Claim抽出 → ファクトチェック (優先度2)
                     │    ├─ 回答提案生成 (優先度3、UI操作トリガー)
                     │    ├─ 想定質問生成 (優先度4、アイドル時)
                     │    └─ 話者embeddingクラスタリング (優先度5)
                     └─ Session store (transcript全文 + 各種生成結果を永続化)
                          └─ 議事録生成バッチジョブ (会議終了後、非同期)
```

## 3. フロントエンド設計(TypeScript)

### 3.1 音声入力ソースの抽象化

音声入力を「マイク」と「PC内で再生中の音声(オンライン会議相手の声、動画/配信の音声など)」で切り替えて使えるようにする。STT抽象化層(4.2)と同じ考え方で、音声取得側も`AudioSourceProvider`インターフェースで抽象化し、UIからソースを選択できるようにする。

```typescript
interface AudioSourceProvider {
  readonly sourceType: "microphone" | "tab_audio" | "system_audio";
  start(): Promise<MediaStream>;
  stop(): void;
}

class MicrophoneSource implements AudioSourceProvider {
  readonly sourceType = "microphone" as const;
  async start() {
    return navigator.mediaDevices.getUserMedia({ audio: true });
  }
  stop() { /* track.stop() */ }
}

class DisplayAudioSource implements AudioSourceProvider {
  // ピッカーで「タブ」を選ぶか「画面全体」を選ぶかでsourceTypeが決まる
  readonly sourceType: "tab_audio" | "system_audio";
  async start() {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: true, // 仕様上、音声のみの取得はできないため必須
      audio: { systemAudio: "include" },
    });
    stream.getVideoTracks().forEach((t) => t.stop()); // 映像は即座に破棄し音声のみ使用
    return stream;
  }
  stop() { /* track.stop() */ }
}
```

いずれのソースから得た`MediaStream`も、既存どおり`AudioWorkletNode`でPCM 16kHz/mono/16bitに変換してWebSocketへ送信する後段の処理は共通化できる(音声取得部分の差し替えのみで完結する設計)。

**ブラウザ/OSによるサポート状況(2026年8月時点)**

| ブラウザ / OS | マイク | タブ音声 | システム全体の音声 |
|---|---|---|---|
| Chrome / Edge (Windows) | ○ | ○ | ○(画面全体を共有時) |
| Chrome / Edge (macOS) | ○ | ○ | ○(Chrome 141以降 かつ macOS 14.2以降) |
| Firefox / Safari | ○ | ×(APIはあるが音声取得は無視される) | × |

- `getDisplayMedia`は仕様上ビデオトラックの取得が必須。映像は取得直後に停止・破棄し、音声のみ後段パイプラインに渡す。
- Firefox/Safariでは「PC音声」の選択肢自体をUI上で無効化し、マイクのみにフォールバックさせる(User-Agent Client Hints等でブラウザ判定)。
- `getDisplayMedia`はマイクと異なりセキュリティ上の理由から毎セッションごとにユーザーがピッカーで対象(タブ/画面)を選び直す必要があり、一度の許可で自動再利用はできない。この操作コストをUI上で分かりやすく案内する。
- クライアント側VAD(voice activity detection、エネルギー閾値+ゼロクロス率、または`@ricky0123/vad-web`等)は音声ソースによらず共通で適用し、無音区間の送信を抑制して帯域とサーバー負荷を削減する。発話区間の切れ目は後段の話者分離にも利用できる。
- 20〜100msごとにチャンクをWebSocketでバイナリ送信。接続断への対応として、送信バッファ+再接続時のリシンクロ機構(シーケンス番号を付与)を用意。

### 3.2 UI構成(優先度どおりに配置)

| パネル | 内容 | 更新方式 |
|---|---|---|
| 上部: 音声ソース切替 | 「マイク」「タブ音声」「システム音声」を選択(ブラウザ非対応の選択肢は無効化表示) | セッション開始前に選択、開始後はセッション再接続を伴う |
| メイン: ライブ文字起こし | 確定/未確定テキスト、後から話者タグが付与されれば色分け | WebSocketのtranscriptイベントで逐次更新 |
| サイド: ファクトチェック | 話題になった主張・検証結果・出典リンク(ON/OFF切替可能) | fact_checkイベントで非同期追加 |
| ポップアップ/カード: 回答提案 | 「回答提案」ボタン押下で直近の問いへの回答例を提示、コピーボタン | 押下時に生成中カード → answer_suggestionイベントで確定表示 |
| サイド下部: 想定質問リスト | 「次に聞かれそうな質問」+回答例、随時更新 | anticipated_qaイベントで追加/更新 |
| 文字起こし内: 話者ラベル | Speaker A/B等の色分けタグ(精度はベストエフォートと明示) | speaker_updateイベントで既存行を再ラベル |
| フッター | 「議事録を生成」ボタン → 生成中ステータス → 完了後に閲覧/ダウンロード | REST APIポーリング or 通知 |

各パネルは独立してレンダリングし、対応するイベントが来なくてもメイン機能(文字起こし)を阻害しない設計とする。

## 4. バックエンド設計(FastAPI)

### 4.1 WebSocketメッセージプロトコル

**Client → Server**
```jsonc
// 音声データはバイナリフレームで送信(制御メッセージと分離)
{ "type": "start_session", "stt_provider": "cloud" | "local", "audio_source": "microphone" | "tab_audio" | "system_audio", "features": ["fact_check", "answer_suggestion", "anticipated_qa", "diarization"] }
{ "type": "stop_session" }
```

**Server → Client**
```jsonc
{ "type": "transcript", "segment_id": "s1", "speaker_id": null, "audio_source": "microphone", "text": "...", "is_final": true, "start_ts": 12.3, "end_ts": 14.1 }
{ "type": "fact_check", "ref_segment_id": "s1", "claim": "...", "verdict": "supported" | "disputed" | "unclear", "sources": [{"title": "...", "url": "..."}] }
{ "type": "answer_suggestion", "ref_segment_id": "s3", "question": "...", "suggested_answer": "..." }
{ "type": "anticipated_qa", "question": "...", "suggested_answer": "..." }
{ "type": "speaker_update", "segment_id": "s1", "speaker_id": "spk_1" }
{ "type": "status", "stage": "stt_connected" | "processing" | "error", "message": "..." }
```

イベントに `type` の判別子を持たせ、フロント側では型ごとに専用のReducerで状態更新する(優先度1のtranscriptイベントの処理は他のイベント処理と非同期に独立させる)。

### 4.2 STT抽象化レイヤー

```python
class SttProvider(Protocol):
    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]: ...

class CloudSttProvider(SttProvider):
    # Google Cloud Speech-to-Text streaming / Azure Speech / OpenAI Realtime API 等
    ...

class LocalSttProvider(SttProvider):
    # faster-whisper / whisper-streaming をEVO-X2上のGPUで実行
    ...
```

- Factory + 設定駆動で `stt_provider` を選択(セッション開始時にクライアントが指定、またはサーバー側デフォルト)。
- 両実装とも入力は共通フォーマット(16kHz mono PCM16)に統一し、差し替え可能にする。
- ローカルSTTはプライバシー要件(社外秘の会議など)やコスト削減、クラウドSTTは精度・低レイテンシ・多言語対応で使い分けるユースケースを想定。
- `TranscriptEvent` は `is_final=False` の暫定テキスト(即座に画面へ)と `is_final=True` の確定テキスト(後続の分析パイプラインのトリガー)を区別する。

### 4.3 MeetingSessionオーケストレーター

WebSocket接続ごとに1つの `MeetingSession` インスタンスを保持し、以下を管理する:

- ローリングな会話バッファ(直近N分のtranscript、トピック履歴)
- 確定transcriptセグメントを受け取るたびに、非同期タスクをディスパッチ(`asyncio.create_task`、または負荷が増えた場合は `arq` + Redisのタスクキューに移行)
- 各タスクは独立して失敗してもよい(例外はログに残しUIには影響させない)設計とし、文字起こしパイプラインとは疎結合にする

```python
async def on_final_segment(self, segment: TranscriptEvent):
    await self.broadcast(segment)  # 優先度1: 即座に配信
    asyncio.create_task(self._maybe_fact_check(segment))       # 優先度2
    self._maybe_schedule_anticipated_qa()                       # 優先度4(間引き実行)
    asyncio.create_task(self._update_speaker_clustering(segment)) # 優先度5
```

優先度3(回答提案)はセグメント確定ではなく**UIからのリクエストを起点に発火する**(4.4参照)ため、この経路には含まれない。

```python
async def request_answer_suggestion(self, request: AnswerSuggestionRequest):
    asyncio.create_task(self._generate_answer_suggestion(request))  # 優先度3
```

### 4.4 各分析タスクの設計

**ファクトチェック/情報収集(優先度2)**
- 確定セグメントごとに軽量な分類(LLM function-callingまたは正規表現+キーワード)で「検証可能な主張が含まれるか」を判定。
- 該当すればLLM(Claude API)+ Web検索ツールで検証し、`verdict` と出典を返す。コスト・レイテンシが大きいため、頻度を間引く(例: 直近30秒以内に同一トピックを検証済みならスキップ)。
- ON/OFF切替可能にし(「任意で」という要件どおり)、デフォルトOFFも選択肢に入れておくとよい。

**回答提案(優先度3)**

発火のトリガーは**UIからのユーザー操作(ボタン押下)を主軸とする**。当初はLLMによる自動質問検出を主軸に据えていたが、以下の理由で手動トリガーを主軸に変更した。

- **誤検知コストが非対称**: 誤検出は画面ノイズとLLMコストを生み、見逃しは機能そのものを無価値にする。手動トリガーはどちらも発生しない。
- **「回答提案が欲しいタイミング」は質問された瞬間と一致しない**: 質問が言い終わる前に助けが欲しい場合もあり、逆に自分で答えられる質問には提案が不要。この判断はユーザーの内部状態に依存するため、発話内容からの推論では原理的に代替できない。
- **レイテンシ予算が単純化**: 検出用のLLM呼び出し(0.5〜1秒)が不要になり、2〜3秒目標の予算を回答生成に全振りできる。

処理の流れ:

1. クライアントが `request_answer_suggestion`(`request_id` と押下時刻を含む)を送信する。
2. **grace period**: 押下時点では質問の末尾がまだ確定していない可能性が高い(VADの無音待ち + STT処理の遅延)。押下を「リクエストの予約」として扱い、押下時刻以降に確定するセグメントを短時間(1.5秒程度)待ってからコンテキストを確定させる。待機中はUIに「生成中」カードを表示する。
3. **コンテキスト組み立て**: 直近セグメントを質問候補、その前を文脈として分けてLLMに渡す。「どこからどこまでが質問か」の境界は厳密に決めず、プロンプト側(「直近の会話の末尾にある、あなたに向けられた問いに答えてください」)で吸収する。ユーザーに範囲選択を求めないことで、操作コストを1クリックに保つ。
4. **生成**: 低レイテンシ重視のため軽量モデル(例: Claude Haiku相当)を優先し、必要なら詳細版を後追いで再生成する2段階方式も検討可。
5. `answer_suggestion` イベント(`request_id`・`status`・回答本文・参照した `segment_id`)で結果を配信する。

LLMによる自動質問検出は**サブ軸**として後続フェーズで追加する(Phase 2.5)。手動運用の実データ(どのタイミングで押されたか)が蓄積されてから着手することで、検出ロジックの設計精度を上げられる。追加時は、自動カードと手動カードの重複排除、ON/OFF切替、誤検出カードの却下操作が必要になる。

**想定質問生成(優先度4)**
- リアルタイム性は不要なため、発話の合間(無音区間検出時)やN分ごとのバッチで、直近のトピックから「聞かれそうな質問」をLLMに生成させ、事前に回答案もセットで用意しておく。
- 実際に近い質問が検出された場合は優先度3のフローと統合し、キャッシュ済み回答案を即座に出す最適化も可能。

**話者判別(優先度5)**
- 単一マイクのためチャンネル分離は不可。音声埋め込み(speaker embedding、例: pyannote-audioやresemblyzer)をセグメントごとに算出し、オンラインクラスタリングで話者IDを推定する方式。
- 精度は複数マイクや指向性マイクアレイに比べて劣ることを前提とし、UI上も「参考情報」程度の位置づけにする。将来的にマイクを増設する場合はチャンネルベースの分離に切替可能な設計(SttProviderと同様に抽象化)にしておくと拡張しやすい。
- **補足(音声ソース切替との関係)**: 3.1で追加した`audio_source`は現状「マイク」「PC音声」を排他的に切り替える設計だが、将来的に両方を同時取得できるようにすれば、`audio_source`自体が「自分の発言(マイク)」か「相手の発言(PC音声)」かを示す非常に安価で高精度な話者シグナルになる。埋め込みベースのクラスタリングより先に、この構成による簡易話者分離を検討する価値がある。

### 4.5 LLMレイヤー

- クラウド(Claude API): ファクトチェック、回答提案、想定質問生成、議事録生成など品質が重要な処理。
- ローカルLLM(EVO-X2上でQwen系モデルなど): プライバシー重視モードや、コスト・オフライン要件がある場合の代替パス。SttProviderと同様のインターフェースで切替可能にしておくと、既存の検証結果(Qwen3.6-27B等)を活かせる。
- タスクキュー: 初期は `asyncio` のバックグラウンドタスクで十分だが、同時接続数やタスク数が増えたらRedis + arqなどの永続キューに移行する余地を残す。

## 5. データ永続化

- **セッションストア**: PostgreSQLに `sessions`, `transcript_segments`, `fact_checks`, `qa_suggestions`, `speaker_labels` 等のテーブルで永続化。BigQueryへのエクスポートも既存のログ基盤があれば同じパターンで流用可能(分析・監査用途)。
- **音声データ**: デフォルトは保存しない、または明示的opt-inでのみ一定期間保持(プライバシー配慮)。
- 各生成物(fact_check, answer_suggestion等)は元となった `transcript_segment_id` と紐付けて保存し、議事録生成時に参照できるようにする。

## 6. 議事録生成(会議終了後・非同期でよい)

1. セッション終了(`stop_session` またはWebSocket切断)をトリガーに、バッチジョブをキューイング(FastAPIのBackgroundTasksでも可、規模が大きければ別ワーカー)。
2. セッションストアから全transcript・話者ラベル・Q&A履歴を取得。
3. 長時間会議はmap-reduce方式で要約: チャンクごとに要約 → チャンク要約を統合 → 最終的な構造化議事録(決定事項/アクションアイテム/トピック一覧/主要Q&A)を生成。
4. 完了通知はポーリング用REST API、またはメール/Slack通知(既存インフラがあれば連携)。
5. 出力形式はMarkdown標準とし、必要に応じてWord(.docx)ダウンロードにも対応可能。

「時間を開けてからで構わない」という要件どおり、これはリアルタイムパイプラインと完全に切り離した非同期バッチとして設計し、ライブ処理の負荷やレイテンシに一切影響を与えないようにする。

## 7. 技術スタック推奨

| レイヤー | 推奨技術 |
|---|---|
| フロントエンド | TypeScript + React、WebSocketクライアント、Web Audio API / AudioWorklet、Screen Capture API(`getDisplayMedia`、PC音声取得用) |
| バックエンド | FastAPI + WebSocket(asyncio)、Pydanticでメッセージスキーマ定義 |
| STT(クラウド) | Google Cloud Speech-to-Text streaming / Azure Speech / OpenAI Realtime API |
| STT(ローカル) | faster-whisper または whisper-streaming(EVO-X2 GPU利用) |
| LLM(クラウド) | Claude API(ファクトチェック・回答提案・議事録生成) |
| LLM(ローカル) | Qwen系モデル(Ollama/vLLM、EVO-X2上) |
| 話者分離 | pyannote-audio(embedding + オンラインクラスタリング) |
| ストレージ | PostgreSQL(セッション/transcript永続化)、必要に応じてBigQuery連携 |
| タスク処理 | asyncioタスク → 規模拡大時はarq/Redis |

## 8. 実装ロードマップ(フェーズ分割)

1. **Phase 1(MVP)**: 音声入力ソース切替(マイク/PC音声)・WebSocketストリーミング・STT抽象化(クラウド/ローカル)・ライブ文字起こし表示
2. **Phase 2**: 回答提案(優先度3、UI操作トリガー) / **Phase 2.5**: LLMによる自動質問検出(サブ軸)
3. **Phase 3**: ファクトチェック/情報収集パイプライン(優先度2)
4. **Phase 4**: 想定質問生成(優先度4)
5. **Phase 5**: 話者判別(優先度5、精度はベストエフォートとして提供)
6. **Phase 6**: 議事録生成バッチジョブ

各フェーズは独立して動作確認できるよう、非同期タスクとして疎結合に追加していく設計にしているため、MVP完成後は機能ごとに段階的にリリース可能。

## 9. 留意点

- **単一マイクでの話者分離には限界がある**ことをユーザーにも明示し、精度への期待値を調整すること。将来的にマイクアレイやチャンネル分離マイクを追加する場合は、SttProvider同様に音声入力ソースを抽象化しておくと拡張しやすい。
- **コスト管理**: ファクトチェック・回答提案・想定質問生成はいずれもLLM呼び出しを伴うため、頻度制御(間引き)とON/OFF切替を必ず用意する。既存のLLMコスト監視ダッシュボード(BigQuery連携)があれば、本アプリのAPI呼び出しも同じ基盤に統合してモニタリングするとよい。
- **プライバシー**: 音声・transcriptの保存範囲、社外秘会議でのローカルLLM/ローカルSTT優先モードなど、利用シーンに応じた設定を用意する。
- **音声入力ソースの切り替え**: PC音声(タブ/システム)の取得はChrome/Edgeでのみ安定動作し、Firefox/Safariは非対応(マイクのみへフォールバック)。また`getDisplayMedia`はセッションごとにユーザーによるピッカー選択が必要という仕様上の制約があるため、UIで明示し操作コストを想定しておく。
