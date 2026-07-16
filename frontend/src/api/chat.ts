import { apiClient } from './client'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sql?: string
  rows?: number
  duration_ms?: number
  error?: string
}

export type ChatBackend = 'local' | 'dgx'

interface ChatRequest {
  message: string
  context?: { question: string; sql?: string }[]
  backend?: ChatBackend
}

interface ChatResponse {
  answer: string
  sql?: string
  rows: number
  duration_ms: number
  model: string
  error?: string
}

interface ChatStatus {
  chat_enabled: boolean
  ollama: {
    status: string
    models?: string[]
    required_model?: string
    error?: string
  }
  dgx_configured?: boolean
  dgx_model?: string | null
}

export const chatApi = {
  async send(
    message: string,
    context?: ChatMessage[],
    backend: ChatBackend = 'local',
  ): Promise<ChatResponse> {
    // Build context from previous turns
    const ctx = context
      ?.filter((m) => m.role === 'user')
      .map((m, i) => ({
        question: m.content,
        sql: context?.[i + 1]?.sql,
      }))
      .slice(-4)

    // Use 120s timeout for chat — CPU inference is slow (~20-40s per LLM call)
    const { data } = await apiClient.post<ChatResponse>('/chat', {
      message,
      context: ctx?.length ? ctx : undefined,
      backend,
    } as ChatRequest, { timeout: 120_000 })
    return data
  },

  async status(): Promise<ChatStatus> {
    const { data } = await apiClient.get<ChatStatus>('/chat/status')
    return data
  },
}
