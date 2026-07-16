import { useState, useRef, useEffect } from 'react'
import { Send, Loader2, ChevronDown, Database, Clock, MessageSquare, Trash2, Cpu, Cloud, AlertTriangle } from 'lucide-react'
import { chatApi, type ChatMessage, type ChatBackend } from '@/api/chat'

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'
  const [showSql, setShowSql] = useState(false)

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[75%] rounded-xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? 'bg-brand-600 text-white rounded-br-sm'
            : 'bg-gray-800 text-gray-200 rounded-bl-sm border border-gray-700'
        }`}
      >
        <p className="whitespace-pre-wrap">{msg.content}</p>

        {!isUser && (msg.sql || msg.duration_ms) && (
          <div className="mt-2 pt-2 border-t border-gray-700/50 space-y-1">
            {msg.duration_ms != null && (
              <div className="flex items-center gap-1 text-[10px] text-gray-500">
                <Clock size={10} />
                {(msg.duration_ms / 1000).toFixed(1)}s
                {msg.rows != null && <span className="ml-2">{msg.rows} rows</span>}
              </div>
            )}
            {msg.sql && (
              <>
                <button
                  onClick={() => setShowSql(!showSql)}
                  className="flex items-center gap-1 text-[10px] text-brand-400 hover:text-brand-300"
                >
                  <Database size={10} />
                  {showSql ? 'Hide SQL' : 'Show SQL'}
                  <ChevronDown size={10} className={`transition-transform ${showSql ? 'rotate-180' : ''}`} />
                </button>
                {showSql && (
                  <pre className="mt-1 p-2 bg-gray-900 rounded text-[10px] text-gray-400 overflow-x-auto font-mono">
                    {msg.sql}
                  </pre>
                )}
              </>
            )}
          </div>
        )}

        {msg.error && (
          <p className="mt-1 text-[10px] text-red-400">{msg.error}</p>
        )}
      </div>
    </div>
  )
}

const SUGGESTIONS = [
  'How much total storage capacity is being utilized?',
  'Which arrays are over 80% utilized?',
  'How many open critical alerts do we have?',
  'Show me capacity breakdown by vendor',
  'Which array had the highest IOPS in the last 24 hours?',
  'Show me uptime for all arrays',
]

// Persist chat across navigation using sessionStorage (clears on F5)
function loadMessages(): ChatMessage[] {
  try {
    const stored = sessionStorage.getItem('usm_chat_messages')
    return stored ? JSON.parse(stored) : []
  } catch { return [] }
}

function saveMessages(msgs: ChatMessage[]) {
  try { sessionStorage.setItem('usm_chat_messages', JSON.stringify(msgs)) } catch {}
}

export default function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>(loadMessages)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [backend, setBackend] = useState<ChatBackend>('local')
  const [dgxConfigured, setDgxConfigured] = useState(false)
  const [dgxModel, setDgxModel] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    // Only offer the DGX toggle if the backend says it's configured.
    chatApi.status()
      .then((s) => { setDgxConfigured(!!s.dgx_configured); setDgxModel(s.dgx_model ?? null) })
      .catch(() => setDgxConfigured(false))
  }, [])

  const isDgx = backend === 'dgx'

  // Sync to sessionStorage whenever messages change
  useEffect(() => {
    saveMessages(messages)
  }, [messages])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  async function handleSend(text?: string) {
    const msg = (text || input).trim()
    if (!msg || loading) return

    const userMsg: ChatMessage = { role: 'user', content: msg }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const resp = await chatApi.send(msg, messages, backend)
      const assistantMsg: ChatMessage = {
        role: 'assistant',
        content: resp.answer,
        sql: resp.sql,
        rows: resp.rows,
        duration_ms: resp.duration_ms,
        error: resp.error ?? undefined,
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Unable to reach the chat service. Make sure Ollama is running.',
          error: err?.message,
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <MessageSquare size={20} className="text-brand-400" />
          <h1 className="text-xl font-semibold text-gray-200">Storage AI Assistant</h1>
          {/* Backend selector — only shown when a DGX endpoint is configured. */}
          {dgxConfigured ? (
            <div className="flex items-center rounded-full bg-gray-800 border border-gray-700 p-0.5 text-[11px]">
              <button
                onClick={() => setBackend('local')}
                disabled={loading}
                className={`flex items-center gap-1 px-2.5 py-0.5 rounded-full transition-colors ${
                  !isDgx ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-gray-200'
                }`}
                title="qwen2.5:3b on CPU — all data stays on this host"
              >
                <Cpu size={11} /> Local
              </button>
              <button
                onClick={() => setBackend('dgx')}
                disabled={loading}
                className={`flex items-center gap-1 px-2.5 py-0.5 rounded-full transition-colors ${
                  isDgx ? 'bg-amber-600 text-white' : 'text-gray-400 hover:text-gray-200'
                }`}
                title={`${dgxModel ?? 'DGX'} — sends questions and result rows OFF-NETWORK`}
              >
                <Cloud size={11} /> DGX
              </button>
            </div>
          ) : (
            <span className="text-[10px] text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full border border-gray-700">
              POC · qwen2.5:3b · Local
            </span>
          )}
        </div>
        {messages.length > 0 && (
          <button
            onClick={() => { setMessages([]); sessionStorage.removeItem('usm_chat_messages') }}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 bg-gray-800/50 hover:bg-gray-800 px-3 py-1.5 rounded-lg transition-colors"
          >
            <Trash2 size={12} />
            Clear chat
          </button>
        )}
      </div>

      {/* Chat area */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* Messages */}
        <div className="flex-1 flex flex-col card p-0 overflow-hidden">
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center space-y-6">
                <div className="w-16 h-16 rounded-2xl bg-brand-600/10 border border-brand-600/20 flex items-center justify-center">
                  <MessageSquare size={28} className="text-brand-400" />
                </div>
                <div>
                  <p className="text-gray-300 font-medium mb-1">Ask me anything about your storage infrastructure</p>
                  <p className="text-xs text-gray-600">
                    {isDgx
                      ? 'DGX backend — questions and results are sent off-network for inference'
                      : 'All data stays local — no information leaves this server'}
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-2 max-w-lg">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => handleSend(s)}
                      className="text-left text-xs text-gray-400 hover:text-brand-400 bg-gray-800/50 hover:bg-gray-800 border border-gray-700/50 rounded-lg px-3 py-2.5 transition-colors"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <MessageBubble key={i} msg={msg} />
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="bg-gray-800 border border-gray-700 rounded-xl rounded-bl-sm px-4 py-3 flex items-center gap-2">
                  <Loader2 size={14} className="animate-spin text-brand-400" />
                  <span className="text-xs text-gray-500">Generating SQL and querying database...</span>
                </div>
              </div>
            )}
          </div>

          {/* Input */}
          <div className="px-4 py-3 bg-gray-800/30 border-t border-gray-800">
            <div className="flex items-center gap-2 max-w-3xl mx-auto">
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about storage capacity, performance, hosts, alerts..."
                disabled={loading}
                className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-brand-500 disabled:opacity-50"
              />
              <button
                onClick={() => handleSend()}
                disabled={loading || !input.trim()}
                className="p-2.5 bg-brand-600 hover:bg-brand-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg transition-colors"
              >
                <Send size={16} />
              </button>
            </div>
            {isDgx ? (
              <p className="mt-2 text-[10px] text-amber-500/80 text-center flex items-center justify-center gap-1">
                <AlertTriangle size={10} />
                DGX ({dgxModel ?? 'remote'}) · questions and result rows are sent off-network
              </p>
            ) : (
              <p className="mt-2 text-[10px] text-gray-600 text-center">
                Local AI · No data leaves this server · Powered by Ollama
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
