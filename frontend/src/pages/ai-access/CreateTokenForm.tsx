import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
import { createMcpToken } from '@/api/mcpAccess'
import type { McpCreatedToken, McpScope } from '@/api/mcpAccess'
import { getApiErrorMessage } from '@/utils/request'
import { buttonClass, ErrorMessage, inputClass, panelClass, primaryClass } from './shared'

const domainLabels: Record<string, string> = { xianyu: '闲鱼商品与账号', drive: '网盘与同步资料', delivery: '卡券与订单发货', messages: '消息与会话' }

export function CreateTokenForm({ scopes, onCreated, onCancel }: { scopes: McpScope[]; onCreated: (token: McpCreatedToken) => void; onCancel: () => void }) {
  const [name, setName] = useState('')
  const [days, setDays] = useState('30')
  const [selected, setSelected] = useState<string[]>(() => scopes.filter(scope => scope.id.endsWith(':read')).map(scope => scope.id))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const busy = useRef(false)
  const domains = Array.from(new Set(scopes.map(scope => scope.id.split(':')[0])))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy.current) return
    if (!name.trim()) { setError('请为客户端填写名称'); return }
    if (!selected.length) { setError('请至少选择一项权限'); return }
    const expires = Number(days)
    if (!Number.isInteger(expires) || expires < 1 || expires > 90) { setError('有效期需要为 1–90 天的整数'); return }
    busy.current = true
    setSaving(true)
    setError('')
    try {
      const token = await createMcpToken({ name: name.trim(), scopes: selected, expires_in_days: expires })
      onCreated(token)
    } catch (reason) {
      setError(`${getApiErrorMessage(reason, '创建未得到确认')}。若请求中断，请先刷新密钥列表核对，再决定是否重新创建。`)
    } finally {
      busy.current = false
      setSaving(false)
    }
  }

  return <form onSubmit={submit} className={`${panelClass} space-y-5 p-4 sm:p-5`} aria-labelledby="create-token-heading">
    <div><h2 id="create-token-heading" className="font-semibold">创建客户端密钥</h2><p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">每个 AI 客户端使用独立密钥，便于识别调用来源和单独撤销。</p></div>
    <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_180px]"><label className="space-y-2 text-sm"><span className="font-medium">客户端名称</span><input autoFocus className={inputClass} value={name} onChange={event => setName(event.target.value)} maxLength={80} placeholder="例如：我的 Codex 工作电脑" required disabled={saving} /></label><label className="space-y-2 text-sm"><span className="font-medium">有效期（天）</span><input className={inputClass} type="number" min={1} max={90} step={1} value={days} onChange={event => setDays(event.target.value)} required disabled={saving} /></label></div>
    <fieldset disabled={saving} className="space-y-3"><legend className="text-sm font-medium">允许的能力</legend><p className="text-xs leading-6 text-slate-500 dark:text-slate-400">默认只读。按用途选择账号启停、分享、卡券配置或真实订单发货等能力；当前授权覆盖本系统用户所属数据，暂不按单个闲鱼账号细分。</p>
      <div className="divide-y divide-slate-200 rounded-lg border border-slate-200 dark:divide-slate-700 dark:border-slate-700">{domains.map(domain => <fieldset key={domain} className="p-3 sm:p-4"><legend className="sr-only">{domainLabels[domain] || domain}</legend><p className="mb-3 text-sm font-medium">{domainLabels[domain] || domain}</p><div className="grid gap-2 sm:grid-cols-3">{scopes.filter(scope => scope.id.startsWith(`${domain}:`)).map(scope => <label key={scope.id} className={`flex cursor-pointer items-start gap-2 rounded-lg border p-3 text-sm ${selected.includes(scope.id) ? 'border-blue-300 bg-blue-50/50 dark:border-blue-800 dark:bg-blue-950/20' : 'border-slate-200 dark:border-slate-700'}`}><input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0 rounded accent-blue-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" checked={selected.includes(scope.id)} onChange={event => setSelected(values => event.target.checked ? [...values, scope.id] : values.filter(value => value !== scope.id))} /><span className="min-w-0"><span className="block font-medium">{scope.label}</span><span className="mt-1 block text-xs leading-5 text-slate-500 dark:text-slate-400">{scope.description}</span></span></label>)}</div></fieldset>)}</div>
    </fieldset>
    {error && <ErrorMessage message={error} />}
    <div className="flex flex-wrap gap-2"><button type="submit" className={primaryClass} disabled={saving}>{saving && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}{saving ? '创建中…' : '创建密钥'}</button><button type="button" className={buttonClass} disabled={saving} onClick={onCancel}>取消</button></div>
  </form>
}
