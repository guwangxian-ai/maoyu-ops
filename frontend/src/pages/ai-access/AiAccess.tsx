import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { KeyRound, Plug, ScrollText } from 'lucide-react'
import { getMcpConnection } from '@/api/mcpAccess'
import type { McpConnection } from '@/api/mcpAccess'
import { getApiErrorMessage } from '@/utils/request'
import { CallsPanel } from './CallsPanel'
import { ConnectionPanel } from './ConnectionPanel'
import { TokensPanel } from './TokensPanel'
import { ErrorMessage, Loading } from './shared'

const sections = [
  { id: 'connection', label: '接入配置', icon: Plug },
  { id: 'tokens', label: '客户端密钥', icon: KeyRound },
  { id: 'logs', label: '调用记录', icon: ScrollText },
] as const

export function AiAccess() {
  const [params, setParams] = useSearchParams()
  const section = sections.find(value => value.id === params.get('section'))?.id || 'connection'
  const [connection, setConnection] = useState<McpConnection | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const requestId = useRef(0)

  const load = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    setError('')
    try {
      const result = await getMcpConnection()
      if (id === requestId.current) setConnection(result)
    } catch (reason) {
      if (id === requestId.current) setError(getApiErrorMessage(reason, '接入信息加载失败，请确认服务器接入地址已配置'))
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [])

  useEffect(() => { void load(); return () => { requestId.current++ } }, [load])

  const navigate = (value: string) => setParams(previous => {
    const next = new URLSearchParams(previous)
    if (value === 'connection') next.delete('section')
    else next.set('section', value)
    return next
  })

  return <section className="mx-auto max-w-6xl space-y-5 text-slate-900 dark:text-slate-100">
    <header><h1 className="text-xl font-semibold">AI 接入</h1><p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">为 AI 客户端配置闲鱼与网盘能力，并管理独立授权。</p></header>
    <nav aria-label="AI 接入分区" className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-700">{sections.map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-current={section === id ? 'page' : undefined} onClick={() => navigate(id)} className={`inline-flex min-h-[44px] shrink-0 items-center gap-2 border-b-2 px-3 pb-3 pt-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${section === id ? 'border-blue-600 text-blue-600 dark:text-blue-400' : 'border-transparent text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'}`}><Icon className="h-4 w-4" aria-hidden="true" />{label}</button>)}</nav>
    {error && <ErrorMessage message={error} retry={() => void load()} />}
    {section === 'connection' && (loading ? <Loading label="正在读取接入配置…" /> : connection && <ConnectionPanel connection={connection} onOpenKeys={() => navigate('tokens')} />)}
    {section === 'tokens' && <TokensPanel connection={connection} />}
    {section === 'logs' && <CallsPanel />}
  </section>
}
