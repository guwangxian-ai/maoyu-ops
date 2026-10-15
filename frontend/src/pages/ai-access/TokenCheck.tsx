import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, Copy, Eye, EyeOff, Loader2 } from 'lucide-react'
import { checkMcpConnection } from '@/api/mcpAccess'
import type { McpConnectionCheck, McpCreatedToken } from '@/api/mcpAccess'
import { getApiErrorMessage } from '@/utils/request'
import { copyToClipboard } from '@/utils/clipboard'
import { useUIStore } from '@/store/uiStore'
import { buttonClass, ErrorMessage, formatTime, inputClass, panelClass, primaryClass } from './shared'

export function TokenCheck({ created, onDismiss }: { created?: McpCreatedToken | null; onDismiss?: () => void }) {
  const [input, setInput] = useState('')
  const [visible, setVisible] = useState(false)
  const [checking, setChecking] = useState(false)
  const [result, setResult] = useState<McpConnectionCheck | null>(null)
  const [error, setError] = useState('')
  const busy = useRef(false)
  const addToast = useUIStore(state => state.addToast)

  const check = async (event: FormEvent) => {
    event.preventDefault()
    const token = created?.token || input.trim()
    if (busy.current || !token) return
    busy.current = true
    setChecking(true)
    setError('')
    setResult(null)
    setInput('')
    try {
      const response = await checkMcpConnection(token)
      if (!response.valid) throw new Error(response.message || '密钥验证未通过')
      setResult(response)
    } catch (reason) {
      setError(getApiErrorMessage(reason, '密钥验证失败，请检查有效期和权限'))
    } finally {
      busy.current = false
      setChecking(false)
    }
  }

  const copyKey = async () => {
    if (!created) return
    const copied = await copyToClipboard(created.token)
    addToast({ type: copied ? 'success' : 'error', message: copied ? '密钥已复制，请保存到客户端的私密配置' : '复制失败，可显示密钥后手动复制' })
  }

  return <section className={`${panelClass} p-4 sm:p-5`} aria-labelledby={created ? 'new-token-heading' : 'check-token-heading'}>
    <h2 id={created ? 'new-token-heading' : 'check-token-heading'} className="font-semibold">{created ? `已创建：${created.name}` : '验证已有密钥'}</h2>
    <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">{created ? '密钥仅在本次创建后可查看。保存到客户端后关闭此区域，离开页面或刷新后无法再次显示。' : '验证当前登录用户的本站密钥，检查有效期、权限和可用工具数量。'}</p>
    <form onSubmit={check} className="mt-4 space-y-3" autoComplete="off">
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative min-w-0 flex-1"><label htmlFor={created ? 'new-mcp-token' : 'existing-mcp-token'} className="sr-only">{created ? '新建客户端密钥' : '需要验证的客户端密钥'}</label><input id={created ? 'new-mcp-token' : 'existing-mcp-token'} type={created && visible ? 'text' : 'password'} autoComplete="off" autoCapitalize="none" spellCheck={false} readOnly={Boolean(created)} disabled={checking && !created} value={created?.token || input} onChange={event => { setInput(event.target.value); setResult(null); setError('') }} placeholder="粘贴 xy_mcp_ 开头的密钥" className={`${inputClass} font-mono ${created ? 'pr-12' : ''}`} required />{created && <button type="button" aria-label={visible ? '隐藏新密钥' : '显示新密钥'} aria-pressed={visible} onClick={() => setVisible(value => !value)} className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-lg text-slate-500 hover:text-blue-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">{visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button>}</div>
        {created && <button type="button" className={buttonClass} onClick={() => void copyKey()}><Copy className="h-4 w-4" aria-hidden="true" />复制密钥</button>}
        <button type="submit" className={primaryClass} disabled={checking || (!created && !input.trim())}>{checking && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}{checking ? '验证中…' : '验证密钥'}</button>
      </div>
      {created && <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">有效至 {formatTime(created.expires_at)} · 该密钥可操作所选权限内、属于当前系统用户的数据。</p>}
      {error && <ErrorMessage message={error} />}
      {result && <div role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300"><p className="flex items-center gap-2 font-medium"><CheckCircle2 className="h-4 w-4" aria-hidden="true" />密钥有效 · {result.tool_count} 个可用工具</p><p className="mt-1 leading-6">{result.message}</p><p className="mt-1 break-words text-xs leading-5">权限：{result.scopes.join('、')} · 验证于 {formatTime(result.checked_at)}</p></div>}
      <p className="text-xs leading-6 text-slate-500 dark:text-slate-400">此检查不会发布商品或向买家发货，也不能代替 AI 客户端的实际连接检查。{!created && '提交验证后将清空输入的密钥。'}</p>
      {created && <button type="button" className={buttonClass} disabled={checking} onClick={onDismiss}>已保存，关闭密钥</button>}
    </form>
  </section>
}
