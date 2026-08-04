# Meeting Assistant

リアルタイムに会議を補助するWebアプリケーション。マイクまたはPC音声(タブ/システム音声)から会話をリアルタイムに文字起こしし、ファクトチェック・質問への回答提案・想定質問生成・話者判別・議事録生成を行う。

設計の詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) を参照。

> **現状: 設計フェーズ完了、実装未着手**
> `backend/` `frontend/` は現時点で空ディレクトリです。以下は `docs/ARCHITECTURE.md` に基づく実装予定の構成です。

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
| STT(クラウド) | Google Cloud Speech-to-Text streaming / Azure Speech / OpenAI Realtime API |
| STT(ローカル) | faster-whisper / whisper-streaming |
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

- Python 3.11+
- Node.js 20+
- PostgreSQL 15+
- Claude API キー(ファクトチェック・回答提案・議事録生成用)
- クラウドSTTを使う場合は対応するAPIキー(Google Cloud Speech-to-Text / Azure Speech / OpenAI Realtime API 等)

### 環境変数

`.env.sample` をコピーして `.env` を作成し、値を設定する。

```bash
cp .env.sample .env
```

`.env` はAPIキーなどの機密情報を含むため、**絶対にコミットしない**(`.gitignore` で除外済み)。

### バックエンド(FastAPI)

`backend/` に実装後、以下を想定:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### フロントエンド(React)

`frontend/` に実装後、以下を想定:

```bash
cd frontend
npm install
npm run dev
```

デフォルトでフロントエンドは `http://localhost:3000`、バックエンドは `http://localhost:8000`(WebSocket: `ws://localhost:8000/ws`)で起動する想定。

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
