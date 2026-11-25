import { apiClient } from './client'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sql?: string
  rows?: number
  duration_ms?: number
  error?: string
  id?: number          // chat_log id, for feedback
  route?: string       // intent name | sql | forecast
  rating?: 1 | -1
  feedbackDone?: boolean
  pending?: boolean    // still streaming
  status?: string      // progress text while pending
}

export interface ChatIntent {
  name: string
  description: string
  example: string
}

export interface FeedbackResult {
  id: number
  rating: 1 | -1
  learned: string[]
}

interface ChatRequest {
  message: string
  context?: { question: string; sql?: string }[]
}

interface ChatResponse {
  answer: string
  sql?: string
  rows: number
  duration_ms: number
  model: string
  error?: string
  id?: number
  route?: string
}

interface ChatStatus {
  chat_enabled: boolean
  ollama: {
    status: string
    models?: string[]
    required_model?: string
    error?: string
  }
}

export const chatApi = {
  async send(
    message: string,
    context?: ChatMessage[],
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
    } as ChatRequest, { timeout: 120_000 })
    return data
  },

  /**
   * Same as send(), streamed (POST /chat/stream, Server-Sent Events): progress
   * text, the SQL once written, and the answer as it is generated. Resolves with
   * the final response. Falls back to send() if streaming isn't available.
   */
  async stream(
    message: string,
    context: ChatMessage[] | undefined,
    on: {
      status?: (text: string) => void
      sql?: (sql: string) => void
      token?: (text: string) => void
    },
  ): Promise<ChatResponse> {
    const ctx = context
      ?.filter((m) => m.role === 'user')
      .map((m, i) => ({ question: m.content, sql: context?.[i + 1]?.sql }))
      .slice(-4)
    let resp: Response
    try {
      resp = await fetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ message, context: ctx?.length ? ctx : undefined }),
      })
    } catch {
      return this.send(message, context)
    }
    if (resp.status === 404 || resp.status === 405 || !resp.body) return this.send(message, context)
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`
      try {
        detail = (await resp.json()).detail ?? detail
      } catch {}
      throw new Error(detail)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let final: ChatResponse | null = null
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, sep)
        buf = buf.slice(sep + 2)
        let event = 'message'
        const data: string[] = []
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
        }
        if (!data.length) continue // heartbeat comment
        const payload = JSON.parse(data.join('\n'))
        if (event === 'status') on.status?.(payload)
        else if (event === 'sql') on.sql?.(payload)
        else if (event === 'token') on.token?.(payload)
        else if (event === 'done') final = payload as ChatResponse
        else if (event === 'error') throw new Error(payload?.detail || 'chat failed')
      }
    }
    if (!final) throw new Error('The answer stream ended early.')
    return final
  },

  async intents(): Promise<ChatIntent[]> {
    const { data } = await apiClient.get<ChatIntent[]>('/chat/intents')
    return data
  },

  async feedback(
    id: number,
    rating: 1 | -1,
    wantedIntent?: string,
    comment?: string,
  ): Promise<FeedbackResult> {
    const { data } = await apiClient.post<FeedbackResult>('/chat/feedback', {
      id,
      rating,
      wanted_intent: wantedIntent || undefined,
      comment: comment || undefined,
    })
    return data
  },

  async status(): Promise<ChatStatus> {
    const { data } = await apiClient.get<ChatStatus>('/chat/status')
    return data
  },
}
