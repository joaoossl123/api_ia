/**
 * Base URL do backend.
 * Em desenvolvimento (vite dev): usa URL relativa + proxy em vite.config.ts → evita CORS.
 * Em produção (build): aponta para o API (ajuste com VITE_URL_API_BASE na build).
 *
 * .env (opcional): VITE_URL_API_BASE=http://127.0.0.1:8000
 */
const envRaw = import.meta.env.VITE_URL_API_BASE as string | undefined
const fromEnv =
  typeof envRaw === 'string' && envRaw.trim().length > 0
    ? envRaw.trim().replace(/\/$/, '')
    : undefined

export const URL_BASE_API: string =
  fromEnv ?? (import.meta.env.DEV ? '' : 'http://127.0.0.1:8000')

export const MENSAGEM_CARREGANDO = 'A processar, aguarde…'
