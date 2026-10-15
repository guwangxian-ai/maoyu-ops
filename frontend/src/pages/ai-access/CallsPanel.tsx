import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'
import { getMcpLogs, getMcpTokens } from '@/api/mcpAccess'
import type { McpCallLog } from '@/api/mcpAccess'
import { getApiErrorMessage } from '@/utils/request'
import { buttonClass, ErrorMessage, formatTime, Loading, panelClass } from './shared'

const PAGE_SIZE = 20

function callStatus(log: McpCallLog) {
  if (log.error_code === 'RUNNING') return { label: '进行中', className: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300' }
  if (log.success) return { label: '成功', className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300' }
  return { label: '未成功', className: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300' }
}

export function CallsPanel() {
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [logs, setLogs] = useState<McpCallLog[]>([])
  const [names, setNames] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const requestId = useRef(0)

  const load = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    setError('')
    try {
      const result = await getMcpLogs(page, PAGE_SIZE)
      if (id === requestId.current) {
        setLogs(result.data || [])
        setTotal(result.total)
      }
    } catch (reason) {
      if (id === requestId.current) setError(getApiErrorMessage(reason, '调用记录加载失败'))
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [page])

  useEffect(() => { void load(); return () => { requestId.current++ } }, [load])
  useEffect(() => {
    let active = true
    void getMcpTokens().then(tokens => {
      if (active) setNames(Object.fromEntries(tokens.map(token => [token.id, token.name])))
    }).catch(() => { /* 日志保留客户端 ID，名称读取失败不阻断审计。 */ })
    return () => { active = false }
  }, [])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <section className={panelClass} aria-labelledby="mcp-calls-heading">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-700 sm:p-5"><div><h2 id="mcp-calls-heading" className="font-semibold">最近调用记录</h2><p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">按客户端追溯 MCP 工具调用。记录不包含密钥原文、请求参数或业务结果正文。</p></div><button type="button" className={buttonClass} disabled={loading} onClick={() => void load()}><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />刷新记录</button></div>
    {error && <div className="p-4"><ErrorMessage message={error} retry={() => void load()} /></div>}
    {loading ? <Loading label="正在读取调用记录…" /> : !error && logs.length === 0 ? <div className="px-4 py-12 text-center"><p className="text-sm font-medium">暂无 MCP 工具调用记录</p><p className="mt-2 text-sm text-slate-500 dark:text-slate-400">AI 客户端执行工具后，会在这里显示真实调用结果。</p></div> : !error && <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-900/50 dark:text-slate-400"><tr>{['时间', '客户端', '工具', '结果', '耗时', '错误代码'].map(label => <th key={label} scope="col" className="whitespace-nowrap px-4 py-3 font-medium">{label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100 dark:divide-slate-700">{logs.map(log => {
      const status = callStatus(log)
      return <tr key={log.id}><td className="whitespace-nowrap px-4 py-3 text-xs">{formatTime(log.created_at)}</td><td className="max-w-48 break-all px-4 py-3"><p>{names[log.token_id] || '客户端'}</p><p className="mt-1 font-mono text-[11px] text-slate-400">{log.token_id}</p></td><td className="max-w-72 break-all px-4 py-3 font-mono text-xs">{log.tool}</td><td className="whitespace-nowrap px-4 py-3"><span className={`rounded px-2 py-1 text-xs ${status.className}`}>{status.label}</span></td><td className="whitespace-nowrap px-4 py-3 text-xs">{log.error_code === 'RUNNING' ? '—' : `${log.duration_ms} ms`}</td><td className="max-w-52 break-all px-4 py-3 font-mono text-xs text-slate-500 dark:text-slate-400">{log.error_code === 'RUNNING' ? '—' : log.error_code || '—'}</td></tr>
    })}</tbody></table></div>}
    {!error && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 dark:border-slate-700"><p className="text-xs text-slate-500 dark:text-slate-400">共 {total} 条 · 第 {page} / {pages} 页</p><div className="flex gap-2"><button type="button" className={buttonClass} disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}><ChevronLeft className="h-4 w-4" aria-hidden="true" />上一页</button><button type="button" className={buttonClass} disabled={loading || page >= pages} onClick={() => setPage(value => value + 1)}>下一页<ChevronRight className="h-4 w-4" aria-hidden="true" /></button></div></div>}
  </section>
}
