import { useCallback, useEffect, useRef, useState } from 'react'
import { Cloud, ExternalLink, Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import {
  cancelQuarkAuthSession, completeQuarkAuthorization, getQuarkAuthSession, getQuarkStatus,
  revokeQuarkAuthorization, startQuarkAuthSession, type QuarkAuthSession, type QuarkStatus,
} from '@/api/quarkDrive'
import { QuarkDialog, quarkButton, quarkInput } from './QuarkTools'

export type AuthorizationIntent = 'login' | 'logout' | 'switch'
const apiError = (reason: unknown) => {
  const failure = reason as { response?: { data?: { detail?: string; message?: string } }; message?: string }
  return failure?.response?.data?.message || failure?.response?.data?.detail || failure?.message || '连接失败，请重试'
}
const extractCode = (input: string) => {
  const value = input.trim()
  if (!/^https?:\/\//i.test(value)) return value
  try { const params = new URL(value).searchParams; return params.get('code') || params.get('agent_auth_code') || '' } catch { return '' }
}

export function QuarkAuthorizationModal({ intent, nickname, onStatus, onClose }: {
  intent: AuthorizationIntent; nickname?: string; onStatus: (status: QuarkStatus) => void; onClose: () => void
}) {
  const [phase, setPhase] = useState<'login' | 'revoke'>(intent === 'login' ? 'login' : 'revoke')
  const [session, setSession] = useState<QuarkAuthSession | null>(null)
  const [url, setUrl] = useState('')
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [manual, setManual] = useState(false)
  const [code, setCode] = useState('')
  const [now, setNow] = useState(Date.now())
  const requestVersion = useRef(0)
  const activeSession = useRef('')
  const alive = useRef(true)
  const callbacks = useRef({ onStatus, onClose })
  callbacks.current = { onStatus, onClose }

  const begin = useCallback(async () => {
    const version = ++requestVersion.current
    setWorking(true); setError(''); setNotice(''); setUrl(''); setSession(null); setCode('')
    try {
      const result = await startQuarkAuthSession()
      if (version !== requestVersion.current || !alive.current) {
        if (result.data?.session_id) void cancelQuarkAuthSession(result.data.session_id).catch(() => {})
        return
      }
      if (!result.success || !result.data) throw new Error(result.message || '获取官方授权二维码失败')
      activeSession.current = result.data.session_id
      setSession(result.data); setUrl(result.data.authorization_url); setNow(Date.now())
    } catch (reason) { if (alive.current && version === requestVersion.current) setError(apiError(reason)) }
    finally { if (alive.current && version === requestVersion.current) setWorking(false) }
  }, [])

  const cancelPending = useCallback(() => {
    alive.current = false; requestVersion.current++
    if (activeSession.current) void cancelQuarkAuthSession(activeSession.current).catch(() => {})
  }, [])
  useEffect(() => { alive.current = true; return cancelPending }, [cancelPending])
  useEffect(() => {
    if (phase !== 'login') return
    // StrictMode cleans up the first setup before this next-turn callback.
    const timer = setTimeout(() => void begin(), 0)
    return () => clearTimeout(timer)
  }, [phase, begin])

  const acceptAuthorization = useCallback(async (expectedSession: string | undefined, version: number, isActive: () => boolean = () => true) => {
    const isCurrent = () => alive.current && isActive() && version === requestVersion.current && (expectedSession === undefined || expectedSession === activeSession.current)
    if (!isCurrent()) return false
    const result = await getQuarkStatus()
    if (!isCurrent()) return false
    if (!result.success || !result.data) throw new Error(result.message || '读取授权状态失败')
    if (!result.data.connected || result.data.verified === false) throw new Error(result.data.message || '授权尚未验证，请检查授权状态')
    callbacks.current.onStatus(result.data); callbacks.current.onClose()
    return true
  }, [])

  const sessionId = session?.session_id
  const sessionStatus = session?.status
  useEffect(() => {
    if (!sessionId || !sessionStatus || !['starting', 'waiting', 'authorized'].includes(sessionStatus) || working) return
    let active = true
    const version = requestVersion.current
    const isCurrent = () => active && alive.current && version === requestVersion.current && sessionId === activeSession.current
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        if (!isCurrent()) return
        if (sessionStatus === 'authorized') { await acceptAuthorization(sessionId, version, isCurrent); return }
        const result = await getQuarkAuthSession(sessionId)
        if (!isCurrent()) return
        if (!result.success || !result.data) {
          setSession(current => current ? { ...current, status: 'failed', message: result.message || '授权会话已结束，请重新获取二维码' } : current)
          return
        }
        setSession(result.data); setNotice('')
        if (['starting', 'waiting'].includes(result.data.status)) timer = setTimeout(() => void poll(), 2000)
      } catch (reason) {
        if (isCurrent()) { setNotice(apiError(reason)); timer = setTimeout(() => void poll(), 5000) }
      }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [sessionId, sessionStatus, working, acceptAuthorization])

  useEffect(() => {
    if (phase !== 'login' || sessionStatus !== 'waiting') return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [phase, sessionStatus])

  const revoke = useCallback(async () => {
    setWorking(true); setError(''); setNotice('')
    try {
      const result = await revokeQuarkAuthorization()
      if (!result.success || !result.data?.authorization_url) throw new Error(result.message || '获取解除授权二维码失败')
      if (alive.current) setUrl(result.data.authorization_url)
    } catch (reason) { if (alive.current) setError(apiError(reason)) }
    finally { if (alive.current) setWorking(false) }
  }, [])

  const checkRevoked = useCallback(async () => {
    const version = requestVersion.current
    const result = await getQuarkStatus()
    if (!alive.current || version !== requestVersion.current) return false
    if (!result.success || !result.data) throw new Error(result.message || '检查授权状态失败')
    if (!result.data.connected && result.data.available) {
      callbacks.current.onStatus(result.data)
      if (intent === 'switch') { setUrl(''); setNotice(''); setPhase('login') }
      else callbacks.current.onClose()
      return true
    }
    setNotice(result.data.verified === false ? result.data.message || '暂时无法确认解绑结果，请稍后检查' : '当前账号仍已授权，请在 App 中确认解除授权')
    return false
  }, [intent])

  useEffect(() => {
    if (phase !== 'revoke' || !url) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const deadline = Date.now() + 5 * 60 * 1000
    const poll = async () => {
      try { if (await checkRevoked()) return } catch (reason) { if (active) setNotice(apiError(reason)) }
      if (active && Date.now() < deadline) timer = setTimeout(() => void poll(), 5000)
    }
    timer = setTimeout(() => void poll(), 5000)
    return () => { active = false; clearTimeout(timer) }
  }, [phase, url, checkRevoked])

  const submitCode = async () => {
    const version = requestVersion.current
    const expectedSession = session?.session_id
    const isCurrent = () => alive.current && version === requestVersion.current && (expectedSession === undefined || expectedSession === activeSession.current)
    const extracted = extractCode(code)
    if (!extracted) { setError('请粘贴授权码或包含 code 的完整跳转地址'); return }
    setWorking(true); setError('')
    try {
      const result = await completeQuarkAuthorization(extracted, expectedSession)
      if (!isCurrent()) return
      if (!result.success) throw new Error(result.message || '授权码无效或已过期')
      await acceptAuthorization(expectedSession, version, isCurrent)
    } catch (reason) { if (isCurrent()) setError(apiError(reason)) }
    finally { if (isCurrent()) setWorking(false) }
  }

  const remaining = Math.max(0, Math.ceil(((session?.expires_at || 0) - now) / 1000))
  const ended = !!session && ['expired', 'failed', 'cancelled'].includes(session.status)
  const title = phase === 'login' ? '登录夸克网盘' : intent === 'switch' ? '切换夸克账号' : '退出夸克账号'
  return <QuarkDialog compact title={title} busy={false} error={error} onClose={onClose}>
    <div className="space-y-5 text-center">
      <p className="text-sm text-slate-500">{phase === 'login' ? '扫码完成夸克官方授权' : `先解除${nickname ? `「${nickname}」` : '当前账号'}对本设备的授权`}</p>
      {url ? <div className="relative mx-auto w-fit rounded-lg border border-slate-200 bg-white p-4">
        <QRCodeSVG value={url} size={224} level="M" marginSize={2} title={phase === 'login' ? '夸克官方登录授权二维码' : '夸克官方解除授权二维码'} />
        {ended && <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-white/95"><button type="button" disabled={working} className="flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm text-white" onClick={() => void begin()}><RefreshCw className="h-4 w-4" />重新获取二维码</button></div>}
      </div> : <div className="mx-auto flex h-60 w-60 flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-slate-200 bg-slate-50 dark:border-slate-600 dark:bg-slate-900/40">
        {working ? <Loader2 className="h-8 w-8 animate-spin text-blue-500" /> : <Cloud className="h-12 w-12 text-blue-500" />}
        <span className="text-sm text-slate-500">{working ? '正在连接官方授权服务…' : phase === 'revoke' ? '确认后生成官方解绑二维码' : '二维码尚未生成'}</span>
      </div>}
      <p className="text-sm font-medium">使用夸克网盘{phase === 'revoke' ? '独立 ' : ' '}App 扫码并确认{phase === 'login' ? '授权' : '解除授权'}</p>
      {phase === 'login' ? <>
        <div className="space-y-2 border-t border-slate-100 pt-4 dark:border-slate-700">
          <p role="status" className="flex items-center justify-center gap-2 text-sm text-slate-700 dark:text-slate-200">{sessionStatus === 'waiting' && <Loader2 className="h-4 w-4 animate-spin text-blue-600" />}{ended ? session.message : sessionStatus === 'authorized' ? '正在验证已保存的授权…' : working ? '正在获取授权地址…' : '等待扫码并确认授权'}{sessionStatus === 'waiting' && remaining > 0 && <span className="text-xs tabular-nums text-slate-400">{remaining}s</span>}</p>
          <p className="text-xs text-slate-500">授权成功后自动加载文件，刷新页面后保留登录状态。</p>
        </div>
        <div className="flex flex-wrap justify-center gap-2"><button type="button" className={quarkButton} disabled={working} onClick={() => void begin()}><RefreshCw className="h-4 w-4" />刷新二维码</button>{url && <a className={quarkButton} href={url} target="_blank" rel="noopener noreferrer"><ExternalLink className="h-4 w-4" />打开官方授权页</a>}</div>
        <p className="text-xs text-slate-500">遇到问题？<button type="button" className="ml-1 text-blue-600 hover:underline" onClick={() => setManual(value => !value)}>使用授权码</button></p>
        {manual && <form className="space-y-3 border-t border-slate-100 pt-4 text-left dark:border-slate-700" onSubmit={event => { event.preventDefault(); void submitCode() }}><label className="block space-y-2 text-sm"><span>在官方授权页完成后，粘贴授权码或完整跳转地址</span><input aria-label="夸克授权码" type="password" autoComplete="off" className={quarkInput} value={code} onChange={event => setCode(event.target.value)} /></label><button type="submit" disabled={working || !code.trim()} className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{working ? '正在验证…' : '完成授权'}</button></form>}
        <p className="flex items-start gap-2 rounded-md bg-slate-50 p-3 text-left text-xs leading-5 text-slate-500 dark:bg-slate-900/40"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />首次授权默认全部文件，可在夸克 App 的登录授权管理中调整范围。</p>
      </> : <>
        <div className="rounded-md bg-amber-50 p-3 text-left text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">需要在夸克网盘独立 App 中确认，网盘文件不会被删除。官方提示：解绑后该设备上的相关网盘授权将失效。</div>
        {!url ? <button type="button" disabled={working} className="w-full rounded-md bg-blue-600 px-4 py-2.5 text-sm text-white disabled:opacity-50" onClick={() => void revoke()}>{working ? '正在生成…' : '生成解除授权二维码'}</button> : <button type="button" className={quarkButton} onClick={() => { setError(''); void checkRevoked().catch(reason => setError(apiError(reason))) }}><RefreshCw className="h-4 w-4" />我已解除授权，检查状态</button>}
        <p className="text-left text-xs leading-6 text-slate-500">无法扫码时，打开夸克网盘 App → 我的 → 登录授权管理 → 网盘 Skill 授权，进入后解除绑定。{intent === 'switch' && '确认解绑后，这里会自动开始新账号授权。'}</p>
      </>}
      {notice && <p role="status" className="text-sm text-amber-700 dark:text-amber-300">{notice}</p>}
    </div>
  </QuarkDialog>
}
