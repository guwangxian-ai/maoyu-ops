import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Cloud, ExternalLink, Loader2, LogIn, LogOut, RefreshCw, Repeat, User } from 'lucide-react'
import { getQuarkStatus, type QuarkStatus } from '@/api/quarkDrive'
import { QuarkFileManager, formatBytes } from './QuarkFileManager'
import { QuarkDialog } from './QuarkTools'
import { QuarkAuthorizationModal, type AuthorizationIntent } from './QuarkAuthorizationModal'

const vipLabels: Record<string, string> = { NORMAL: '普通用户', VIP: '会员', SVIP: '超级会员' }

export function QuarkDrive() {
  const [status, setStatus] = useState<QuarkStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [intent, setIntent] = useState<AuthorizationIntent | null>(null)
  const [accountInfo, setAccountInfo] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menu = useRef<HTMLDivElement>(null)
  const requestVersion = useRef(0)

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current
    setLoading(true); setError('')
    try {
      const result = await getQuarkStatus()
      if (version !== requestVersion.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取授权状态失败')
      setStatus(result.data)
    } catch (reason) { if (version === requestVersion.current) setError(reason instanceof Error ? reason.message : '读取授权状态失败') }
    finally { if (version === requestVersion.current) setLoading(false) }
  }, [])

  const invalidateStatus = useCallback(() => { requestVersion.current++ }, [])
  useEffect(() => { void refresh(); return invalidateStatus }, [refresh, invalidateStatus])
  useEffect(() => {
    if (!menuOpen) return
    const close = (event: MouseEvent) => { if (!menu.current?.contains(event.target as Node)) setMenuOpen(false) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false) }
    document.addEventListener('mousedown', close); document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', escape) }
  }, [menuOpen])

  const usedPercent = status?.capacity_bytes ? Math.min(100, Math.max(0, (status.used_bytes || 0) / status.capacity_bytes * 100)) : 0
  const open = (value: AuthorizationIntent) => { setMenuOpen(false); setIntent(value) }
  const acceptStatus = (next: QuarkStatus) => { requestVersion.current++; setStatus(next); setLoading(false); setError('') }
  const connectionLabel = status?.verified === false ? '待检查连接' : '已授权'
  const accountControl = status?.connected ? (
    <div ref={menu} className="relative shrink-0">
      <button
        type="button"
        aria-label="夸克账号菜单"
        aria-expanded={menuOpen}
        aria-haspopup="menu"
        title={`${status.nickname || '夸克账号'} · ${connectionLabel}`}
        className="flex h-10 items-center gap-2 rounded-md px-2 text-left transition-colors hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 dark:hover:bg-slate-800"
        onClick={() => setMenuOpen(value => !value)}
      >
        <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 dark:bg-slate-800">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <User className="h-4 w-4" />}
          <span aria-label={connectionLabel} className={`absolute bottom-0 right-0 h-2 w-2 rounded-full ring-2 ring-white dark:ring-slate-900 ${status.verified === false ? 'bg-amber-500' : 'bg-green-500'}`} />
        </span>
        <span className="max-w-20 truncate whitespace-nowrap text-sm font-medium sm:max-w-28">{status.nickname || '夸克账号'}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
      </button>
      {menuOpen && (
        <div role="menu" aria-label="夸克账号操作" className="absolute right-0 top-full z-30 mt-2 w-64 rounded-lg border border-slate-200 bg-white p-1.5 shadow-lg dark:border-slate-700 dark:bg-slate-800">
          <div className="mb-1 border-b border-slate-100 px-3 py-3 dark:border-slate-700">
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-sm font-medium">{status.nickname || '夸克账号'}</span>
              <span className={`shrink-0 text-xs ${status.verified === false ? 'text-amber-600 dark:text-amber-400' : 'text-green-600 dark:text-green-400'}`}>{connectionLabel}</span>
            </div>
            <p className="mt-2 text-xs tabular-nums text-slate-500">{status.capacity_bytes ? `${formatBytes(status.used_bytes)} / ${formatBytes(status.capacity_bytes)}` : '容量暂不可用'}</p>
            {!!status.capacity_bytes && <div role="progressbar" aria-label="网盘容量使用率" aria-valuenow={Math.round(usedPercent)} aria-valuemin={0} aria-valuemax={100} className="mt-2 h-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"><div className="h-full rounded-full bg-blue-600" style={{ width: `${usedPercent}%` }} /></div>}
          </div>
          <button type="button" role="menuitem" className="flex w-full items-center gap-3 rounded px-3 py-2.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-700" onClick={() => { setMenuOpen(false); setAccountInfo(true) }}><User className="h-4 w-4" />账号信息</button>
          <button type="button" role="menuitem" className="flex w-full items-center gap-3 rounded px-3 py-2.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-700" onClick={() => { setMenuOpen(false); void refresh() }}><RefreshCw className="h-4 w-4" />检查授权状态</button>
          <button type="button" role="menuitem" className="flex w-full items-center gap-3 rounded px-3 py-2.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-700" onClick={() => open('switch')}><Repeat className="h-4 w-4" />切换账号</button>
          <div className="my-1 border-t border-slate-100 dark:border-slate-700" /><button type="button" role="menuitem" className="flex w-full items-center gap-3 rounded px-3 py-2.5 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30" onClick={() => open('logout')}><LogOut className="h-4 w-4" />退出账号</button>
        </div>
      )}
    </div>
  ) : (
    <button type="button" disabled={loading || status?.available === false} onClick={() => open('login')} className="inline-flex h-10 items-center gap-2 whitespace-nowrap rounded-md border border-blue-200 px-3 text-sm font-medium text-blue-600 transition-colors hover:bg-blue-50 disabled:opacity-50 dark:border-blue-800 dark:hover:bg-blue-950/40">{loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}登录夸克网盘</button>
  )
  const statusNotice = (error || status?.message) ? <div className="space-y-2">
    {error && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30"><span>{error}</span><button type="button" onClick={() => void refresh()} disabled={loading} className="underline">重新检查</button></div>}
    {status?.message && <p role="status" className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{status.message}</p>}
  </div> : null

  return <section aria-label="夸克网盘管理" className="overflow-hidden rounded-lg border border-slate-200 bg-white text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
    {status?.connected ? <QuarkFileManager key={status.account_id || 'current'} accountControl={accountControl} statusNotice={statusNotice} /> : <>
      <div className="flex justify-end px-4 pt-4 sm:px-5 lg:px-6">{accountControl}</div>
      {statusNotice && <div className="px-4 pt-3 sm:px-5 lg:px-6">{statusNotice}</div>}
      {loading ? <div role="status" className="flex min-h-[32rem] flex-col items-center justify-center gap-4 text-sm text-slate-500"><Loader2 className="h-7 w-7 animate-spin text-blue-600" />正在检查已保存的授权…</div> : <div className="flex min-h-[32rem] flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-6 flex h-28 w-28 items-center justify-center rounded-full bg-blue-50 text-blue-500 dark:bg-blue-950/40"><Cloud className="h-16 w-16" strokeWidth={1.2} /></div><h2 className="text-xl font-semibold">连接你的夸克网盘</h2><p className="mt-3 max-w-md text-sm leading-7 text-slate-500">授权后即可查看文件、上传资料和生成分享链接。账号凭证保存在服务端，页面刷新后自动恢复。</p>
      <button type="button" disabled={status?.available === false} className="mt-7 inline-flex items-center gap-2 rounded-md bg-blue-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50" onClick={() => open('login')}><LogIn className="h-4 w-4" />登录并授权</button><a href="https://pan.quark.cn/" target="_blank" rel="noopener noreferrer" className="mt-5 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-blue-600">打开夸克网盘官网<ExternalLink className="h-3 w-3" /></a>
      </div>}
    </>}
    {intent && <QuarkAuthorizationModal intent={intent} nickname={status?.nickname} onStatus={acceptStatus} onClose={() => setIntent(null)} />}
    {accountInfo && <QuarkDialog compact title="夸克账号信息" busy={loading} error={error} onClose={() => setAccountInfo(false)}><dl className="space-y-4 text-sm">{[['账号', status?.nickname || '夸克用户'], ['会员', vipLabels[status?.vip_type || ''] || '夸克用户'], ['空间', `${formatBytes(status?.used_bytes)} / ${formatBytes(status?.capacity_bytes)}`], ['授权', status?.verified === false ? '凭证已保存，等待连接验证' : '已授权']].map(([label, value]) => <div key={label} className="flex justify-between gap-6"><dt className="shrink-0 text-slate-500">{label}</dt><dd className="break-all text-right">{value}</dd></div>)}</dl><p className="text-xs leading-6 text-slate-500">可在夸克网盘 App → 我的 → 登录授权管理中调整文件访问范围。</p></QuarkDialog>}
  </section>
}
