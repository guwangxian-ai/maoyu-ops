import { useCallback, useEffect, useRef, useState } from 'react'
import { Plus, RefreshCw } from 'lucide-react'
import { getMcpTokens, revokeMcpToken } from '@/api/mcpAccess'
import type { McpConnection, McpCreatedToken, McpToken } from '@/api/mcpAccess'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'
import { CreateTokenForm } from './CreateTokenForm'
import { TokenCheck } from './TokenCheck'
import { buttonClass, ErrorMessage, formatTime, Loading, panelClass, primaryClass } from './shared'

function tokenStatus(token: McpToken) {
  if (token.revoked) return { label: '已撤销', className: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
  const expiry = Date.parse(token.expires_at)
  if (!Number.isFinite(expiry)) return { label: '有效期未知', className: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300' }
  if (expiry <= Date.now()) return { label: '已过期', className: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300' }
  return { label: '有效', className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300' }
}

export function TokensPanel({ connection }: { connection: McpConnection | null }) {
  const [tokens, setTokens] = useState<McpToken[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<McpCreatedToken | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<McpToken | null>(null)
  const [revoking, setRevoking] = useState(false)
  const revokeBusy = useRef(false)
  const requestId = useRef(0)
  const addToast = useUIStore(state => state.addToast)

  const load = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    setError('')
    try {
      const result = await getMcpTokens()
      if (id === requestId.current) setTokens(result)
    } catch (reason) {
      if (id === requestId.current) setError(getApiErrorMessage(reason, '密钥列表加载失败'))
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [])

  useEffect(() => { void load(); return () => { requestId.current++ } }, [load])

  const onCreated = (token: McpCreatedToken) => {
    setCreating(false)
    setCreated(token)
    void load()
  }

  const revoke = async () => {
    if (!revokeTarget || revokeBusy.current) return
    revokeBusy.current = true
    setRevoking(true)
    try {
      await revokeMcpToken(revokeTarget.id)
      if (created?.id === revokeTarget.id) setCreated(null)
      setTokens(values => values.map(value => value.id === revokeTarget.id ? { ...value, revoked: true } : value))
      setRevokeTarget(null)
      addToast({ type: 'success', message: '密钥已撤销，后续调用将被拒绝' })
      void load()
    } catch (reason) {
      addToast({ type: 'error', message: getApiErrorMessage(reason, '撤销失败，请重试') })
    } finally {
      revokeBusy.current = false
      setRevoking(false)
    }
  }

  return <div className="space-y-5">
    {creating && connection && <CreateTokenForm scopes={connection.scopes} onCreated={onCreated} onCancel={() => setCreating(false)} />}
    {created && <TokenCheck key={created.id} created={created} onDismiss={() => setCreated(null)} />}
    <section className={panelClass} aria-labelledby="client-tokens-heading">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-700 sm:p-5"><div><h2 id="client-tokens-heading" className="font-semibold">客户端密钥</h2><p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">权限属于当前系统用户。列表不保存或显示密钥原文。</p></div><div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} disabled={loading} onClick={() => void load()}><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />刷新</button><button type="button" className={primaryClass} disabled={!connection || creating || Boolean(created)} onClick={() => setCreating(true)}><Plus className="h-4 w-4" aria-hidden="true" />新建密钥</button></div></div>
      {created && <p className="px-4 pt-4 text-xs text-slate-500 dark:text-slate-400 sm:px-5">保存并关闭上方新密钥后，可以继续创建其他客户端授权。</p>}
      {error && <div className="p-4"><ErrorMessage message={error} retry={() => void load()} /></div>}
      {loading && tokens.length === 0 ? <Loading label="正在读取客户端授权…" /> : tokens.length === 0 && !error ? <div className="px-4 py-12 text-center"><p className="text-sm font-medium">尚未创建客户端密钥</p><p className="mt-2 text-sm text-slate-500 dark:text-slate-400">为第一个 AI 客户端创建只读密钥，完成接入检查。</p></div> : <ul className="divide-y divide-slate-200 dark:divide-slate-700" aria-label="客户端授权列表">{tokens.map(token => {
        const status = tokenStatus(token)
        return <li key={token.id} className="grid gap-3 p-4 sm:p-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-start">
          <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="break-all text-sm font-medium">{token.name}</h3><span className={`rounded px-2 py-0.5 text-xs ${status.className}`}>{status.label}</span></div><p className="mt-2 break-all font-mono text-xs text-slate-400">{token.id}</p><details className="mt-2 text-xs"><summary className="cursor-pointer py-1 text-slate-500 dark:text-slate-400">已选权限（{token.scopes.length}）</summary><ul className="mt-1 space-y-1 text-slate-500 dark:text-slate-400">{token.scopes.map(scope => <li key={scope} className="break-words">{connection?.scopes.find(value => value.id === scope)?.label || scope}<code className="ml-1 text-[11px]">({scope})</code></li>)}</ul></details></div>
          <dl className="space-y-1 text-xs leading-6"><div><dt className="inline text-slate-500 dark:text-slate-400">创建于：</dt><dd className="inline">{formatTime(token.created_at)}</dd></div><div><dt className="inline text-slate-500 dark:text-slate-400">到期于：</dt><dd className="inline">{formatTime(token.expires_at)}</dd></div></dl>
          <dl className="space-y-1 text-xs leading-6"><div><dt className="inline text-slate-500 dark:text-slate-400">最近调用：</dt><dd className="inline">{token.last_used_at ? formatTime(token.last_used_at) : '暂无调用记录'}</dd></div>{token.revoked && <div><dt className="inline text-slate-500 dark:text-slate-400">撤销于：</dt><dd className="inline">{formatTime(token.revoked_at)}</dd></div>}</dl>
          <button type="button" className={`${buttonClass} justify-self-start !text-red-600 dark:!text-red-400`} disabled={token.revoked || revoking} onClick={() => setRevokeTarget(token)} aria-label={`撤销 ${token.name} 的密钥`}>{token.revoked ? '已撤销' : '撤销'}</button>
        </li>
      })}</ul>}
    </section>
    {!created && <TokenCheck />}
    <ConfirmModal isOpen={Boolean(revokeTarget)} title="撤销客户端密钥" message={`撤销“${revokeTarget?.name || ''}”后，该客户端的后续请求将被拒绝。已开始的业务操作不会因此自动回滚。`} type="danger" confirmText="撤销密钥" loading={revoking} onConfirm={() => void revoke()} onCancel={() => { if (!revokeBusy.current) setRevokeTarget(null) }} />
  </div>
}
