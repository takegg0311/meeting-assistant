/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** バックエンドWebSocketのURL。vite.config.ts が BACKEND_PORT / VITE_WS_URL から解決して注入する。 */
  readonly VITE_WS_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
