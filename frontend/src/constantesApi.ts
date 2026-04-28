/**
 * Endereço base do backend FastAPI. Pode sobrescrever com .env:
 * VITE_URL_API_BASE=http://127.0.0.1:8000
 */
export const URL_BASE_API: string =
  (import.meta.env.VITE_URL_API_BASE as string | undefined)?.replace(/\/$/, '') ||
  'http://127.0.0.1:8000'

export const MENSAGEM_CARREGANDO = 'A processar, aguarde…'
