import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// `.env` はリポジトリルート(= frontend/ の親)に置いてあるため、Vite の envDir を親へ向ける。
// これがないとルートの `.env` が一切読み込まれず、`VITE_WS_URL` は常に undefined になる。
const ENV_DIR = '..'
const DEFAULT_BACKEND_PORT = '8000'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // 第3引数を '' にすることで、VITE_ プレフィックスの付かない `BACKEND_PORT` も読み込む
  // (プロセスの環境変数も含まれるため `BACKEND_PORT=8001 npm run dev` でも効く)。
  const env = loadEnv(mode, ENV_DIR, '')

  // `VITE_WS_URL` が明示されていればそれを優先し、なければ `BACKEND_PORT` から組み立てる。
  // これによりバックエンドのポートは `BACKEND_PORT` だけで一元管理できる。
  const wsUrl = env.VITE_WS_URL || `ws://localhost:${env.BACKEND_PORT || DEFAULT_BACKEND_PORT}/ws`

  return {
    plugins: [react()],
    envDir: ENV_DIR,
    define: {
      'import.meta.env.VITE_WS_URL': JSON.stringify(wsUrl),
    },
  }
})
