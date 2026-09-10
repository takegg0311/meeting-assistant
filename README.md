# Meeting Assistant

リアルタイムに会議を補助するWebアプリケーション。マイクまたはPC音声(タブ/システム音声)から会話をリアルタイムに文字起こしし、ファクトチェック・質問への回答提案・想定質問生成・話者判別・議事録生成を行う。

設計の詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) を参照。

> **現状: Phase 1(MVP)実装中**
> 音声入力・WebSocketストリーミング・ライブ文字起こし表示、およびSTTプロバイダ(クラウド: OpenAI Realtime / Google Cloud、ローカル: whisper.cpp Vulkan)を実装済み。Phase 2以降(質問検出・ファクトチェック等)は未着手。

## 1. 全体像

文字起こし(最優先・低レイテンシ)のパイプラインを他の分析処理から完全に分離し、絶対にブロックしない設計。ファクトチェック・回答提案・想定質問生成・話者判別は、確定した文字起こしイベントに反応して非同期に発火し、結果が出来次第WebSocketで画面に流し込む。

```
Client (Browser) [音声入力: マイク / PC音声(タブ・システム)を切替]
  └─ WebSocket ─→ FastAPI backend (WebSocketハブ / MeetingSessionオーケストレーター)
                     ├─ STT provider (Cloud / Local 切替可能)
                     │    └─ 確定transcriptイベントをSessionに供給
                     ├─ Analysis pipeline (非同期タスク群、それぞれ独立して発火)
                     │    ├─ Topic/Claim抽出 → ファクトチェック
                     │    ├─ 質問検出 → 回答提案生成
                     │    ├─ 想定質問生成(アイドル時)
                     │    └─ 話者embeddingクラスタリング
                     └─ Session store (transcript全文 + 各種生成結果を永続化)
                          └─ 議事録生成バッチジョブ(会議終了後、非同期)
```

| 機能 | レイテンシ要求 | 実行方式 |
|---|---|---|
| 文字起こし | 数百ms〜1秒 | STTストリーミング、ブロッキングなし |
| ファクトチェック/情報収集 | 数秒(遅延許容) | 非同期タスク、fire-and-forget |
| 質問への回答提案 | 2〜3秒目標 | 非同期タスク、優先度高め |
| 想定質問の生成 | 遅延許容 | 非同期・バックグラウンド |
| 話者判別 | 遅延許容、精度ベストエフォート | 非同期 |
| 議事録生成 | 会議終了後、数分の遅延OK | バッチジョブ |

## 2. 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | TypeScript + React、WebSocketクライアント、Web Audio API / AudioWorklet、Screen Capture API |
| バックエンド | FastAPI + WebSocket(asyncio)、Pydantic |
| STT(クラウド) | OpenAI Realtime API(`gpt-4o-transcribe`) / Google Cloud Speech-to-Text(StreamingRecognize) |
| STT(ローカル) | whisper.cpp(Vulkanビルド、`whisper-cli`をsubprocess実行 + サーバー側VADで発話区切り検出) |
| LLM(クラウド) | Claude API(ファクトチェック・回答提案・議事録生成) |
| LLM(ローカル) | Qwen系モデル(Ollama/vLLM) |
| 話者分離 | pyannote-audio |
| ストレージ | PostgreSQL |
| タスク処理 | asyncio → 規模拡大時はarq/Redis |

## 3. ディレクトリ構成

```
meeting-assistant/
├── backend/     # FastAPI (Python) — WebSocketハブ、STT/LLM連携、分析パイプライン
├── frontend/    # TypeScript + React — 音声入力、ライブ表示UI
├── docs/
│   └── ARCHITECTURE.md   # 詳細設計ドキュメント
├── .env.sample
└── README.md
```

## 4. セットアップ

### 前提

- Python 3.11+ / [uv](https://docs.astral.sh/uv/)(バックエンドの依存管理・仮想環境に使用)
- Node.js 20+
- PostgreSQL 15+(Phase 6以降で使用。Phase 1時点では未使用)
- Claude API キー(ファクトチェック・回答提案・議事録生成用、Phase 2以降)
- 使用するSTTプロバイダに応じた準備(下記「STTプロバイダの設定」参照)

### 環境変数

`.env.sample` をコピーして `.env` を作成し、値を設定する。

```bash
cp .env.sample .env
```

`.env` はAPIキーなどの機密情報を含むため、**絶対にコミットしない**(`.gitignore` で除外済み)。

### バックエンド(FastAPI)

依存管理・仮想環境には [uv](https://docs.astral.sh/uv/) を使用する。

```bash
cd backend
uv sync --extra dev
uv run python -m app.main
```

`uv sync` が `.venv` を作成し、`pyproject.toml` / `uv.lock` に基づいて依存を解決・インストールする。

待受ポートは `.env` の `BACKEND_PORT`(未設定時は8000)で決まる。フロントエンドの接続先も同じ `BACKEND_PORT` から解決されるため、ポートを変えるときは `.env` の1箇所を書き換えるだけでよい。

`uvicorn` コマンドを直接使う場合は `--port` の指定が必須(指定しないと `BACKEND_PORT` に関係なく8000で待受する)。

```bash
uv run uvicorn app.main:app --reload --port "$BACKEND_PORT"
```

### フロントエンド(React)

`frontend/` に実装後、以下を想定:

```bash
cd frontend
npm install
npm run dev
```

デフォルトでフロントエンドは `http://localhost:3000`、バックエンドは `http://localhost:8000`(WebSocket: `ws://localhost:8000/ws`)で起動する。

バックエンドのポートを変える場合は `.env` の `BACKEND_PORT` のみを変更する(例: `BACKEND_PORT=8001`)。フロントエンドのWebSocket接続先は `vite.config.ts` が `BACKEND_PORT` から解決するため追従する。`VITE_WS_URL` を明示した場合はそちらが優先される。

### STTプロバイダの設定

セッション開始時(`start_session`メッセージの`stt_provider`)に選択する。`mock`はAPIキー等の設定不要で常に利用可能(開発・動作確認用のダミー文字起こし)。

| `stt_provider` | 説明 | 必要な設定 |
|---|---|---|
| `mock` | ダミーの文字起こしを返す(開発用) | なし |
| `cloud_openai` | OpenAI Realtime API(`gpt-4o-transcribe`)によるストリーミング認識 | `OPENAI_API_KEY` |
| `cloud_google` | Google Cloud Speech-to-TextのStreamingRecognize | `GOOGLE_APPLICATION_CREDENTIALS`(サービスアカウント鍵JSONのパス) |
| `local_whispercpp` | whisper.cpp(Vulkanビルド等)の`whisper-cli`をVAD区切りごとにsubprocess実行 | `WHISPER_CPP_BINARY`、GGMLモデル |

#### whisper.cpp(ローカル)のセットアップ

1. ホスト上でVulkanビルド済みの`whisper-cli`を用意する(Docker・Pythonバインディングは使わない)。

   ```bash
   git clone https://github.com/ggml-org/whisper.cpp
   cd whisper.cpp
   cmake -B build -DGGML_VULKAN=ON -DGGML_CUDA=OFF -DGGML_HIP=OFF
   cmake --build build --config Release
   ```

2. `.env`の`WHISPER_CPP_BINARY`にバイナリパスを設定する。
3. GGMLモデルを取得する(リポジトリには含めない)。

   ```bash
   cd backend
   uv run python scripts/download_whisper_cpp_model.py base
   ```

4. `WHISPER_CPP_MODEL_DIR`(未設定時は`./models/whisper_cpp`)にモデルが配置されていることを確認する。

AMD Vulkan環境では`WHISPER_CPP_FLASH_ATTN=false`(既定)・`WHISPER_CPP_BEAM_SIZE=5`を推奨する(flash attention有効時や大きなbeam_sizeでは不安定になる報告がある)。

## 5. 実装ロードマップ

1. **Phase 1(MVP)**: 音声入力ソース切替(マイク/PC音声)・WebSocketストリーミング・STT抽象化・ライブ文字起こし表示
2. **Phase 2**: 質問検出 + 回答提案
3. **Phase 3**: ファクトチェック/情報収集パイプライン
4. **Phase 4**: 想定質問生成
5. **Phase 5**: 話者判別(ベストエフォート)
6. **Phase 6**: 議事録生成バッチジョブ

詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) の各セクションを参照。

## 6. 留意点

- 単一マイクでの話者分離には精度上の限界がある。
- ファクトチェック・回答提案・想定質問生成はLLM呼び出しを伴うため、コスト管理(頻度制御・ON/OFF切替)に留意する。
- PC音声(タブ/システム)取得はChrome/Edgeでのみ安定動作し、Firefox/Safariは非対応(マイクのみへフォールバック)。
- 音声・transcriptの保存範囲はプライバシー要件に応じて設定する。
- `local_whispercpp`はVADによる発話区切りごとにサブプロセスを起動するため、クラウド勢(OpenAI Realtime / Google StreamingRecognize)と比べて確定テキストが返るまでの遅延が大きい。社外秘の会議などプライバシー要件がある場合の選択肢として位置づける。
