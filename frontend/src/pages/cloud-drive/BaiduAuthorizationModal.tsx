import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, LogIn, LogOut, RefreshCw } from 'lucide-react'
import {
  cancelBaiduAuthSession, getBaiduAuthConfig, getBaiduAuthSession,
  getBaiduStatus, revokeBaiduAuthorization, startBaiduAuthorization,
  type BaiduAuthConfig, type BaiduAuthSession, type BaiduAuthStart, type BaiduStatus,
} from '@/api/baiduDrive'
import { getApiErrorMessage } from '@/utils/request'
import { QuarkDialog, quarkButton } from './QuarkTools'

export type BaiduAuthorizationIntent = 'login' | 'switch' | 'logout'
const terminalStatuses = new Set(['authorized', 'expired', 'failed', 'cancelled'])
const sessionLabels: Record<BaiduAuthSession['status'], string> = {
  starting: '正在准备二维码', waiting: '等待扫码', scanned: '已扫码，请在手机上确认',
  authorizing: '正在验证登录', authorized: '登录已完成', expired: '二维码已过期', failed: '登录失败', cancelled: '扫码已取消',
}
const validQrImage = (value: string) => /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(value)

export function BaiduAuthorizationModal({ intent, nickname, onStatus, onClose }: {
  intent: BaiduAuthorizationIntent
  nickname?: string
  onStatus: (status: BaiduStatus) => void
  onClose: () => void
}) {
  const [config, setConfig] = useState<BaiduAuthConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [authorization, setAuthorization] = useState<BaiduAuthStart | null>(null)
  const [session, setSession] = useState<BaiduAuthSession | null>(null)
  const mounted = useRef(true)
  const activeSession = useRef<{ id: string; terminal: boolean } | null>(null)
  const accepting = useRef(false)
  const configVersion = useRef(0)
  const callbacks = useRef({ onStatus, onClose })
  callbacks.current = { onStatus, onClose }

  const loadConfig = useCallback(async () => {
    const version = ++configVersion.current
    setLoading(true)
    setError('')
    try {
      const result = await getBaiduAuthConfig()
      if (!mounted.current || version !== configVersion.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取扫码登录配置失败')
      if (result.data.mode !== 'baidupcs-go' || !result.data.supports_qrcode) throw new Error('百度网盘扫码服务尚未准备完成，请稍后重试')
      setConfig(result.data)
    } catch (reason) {
      if (mounted.current && version === configVersion.current) setError(getApiErrorMessage(reason, '读取扫码登录配置失败'))
    } finally { if (mounted.current && version === configVersion.current) setLoading(false) }
  }, [])

  const acceptAuthorized = useCallback(async () => {
    if (accepting.current) return
    accepting.current = true
    if (mounted.current) { setBusy(true); setError('') }
    try {
      const result = await getBaiduStatus()
      if (!result.success || !result.data?.connected || result.data.auth_mode !== 'baidupcs-go') throw new Error(result.message || result.data?.message || '登录状态尚未验证，请稍后重新检查')
      if (!mounted.current) return
      if (activeSession.current) activeSession.current.terminal = true
      callbacks.current.onStatus(result.data)
      callbacks.current.onClose()
    } catch (reason) {
      if (mounted.current) setError(getApiErrorMessage(reason, '检查登录结果失败'))
    } finally {
      accepting.current = false
      if (mounted.current) setBusy(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    if (intent !== 'logout') void loadConfig()
    else setLoading(false)
    return () => {
      mounted.current = false
      const pending = activeSession.current
      if (pending && !pending.terminal) void cancelBaiduAuthSession(pending.id).catch(() => { /* Abandoned sessions also expire on the server. */ })
    }
  }, [intent, loadConfig])

  useEffect(() => {
    if (!authorization) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      const current = activeSession.current
      if (!active || !current || current.id !== authorization.session_id || current.terminal) return
      try {
        const result = await getBaiduAuthSession(current.id)
        if (!active || activeSession.current?.id !== current.id) return
        if (!result.success || !result.data) throw new Error(result.message || '读取扫码进度失败')
        setSession(result.data)
        setNotice('')
        if (terminalStatuses.has(result.data.status)) {
          current.terminal = true
          if (result.data.status === 'authorized') await acceptAuthorized()
          return
        }
      } catch (reason) {
        if (active) setNotice(getApiErrorMessage(reason, '暂时无法读取扫码进度，正在重试'))
      }
      if (active) timer = setTimeout(() => void poll(), 2500)
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [authorization, acceptAuthorized])

  const cancelPending = async () => {
    const current = activeSession.current
    if (!current || current.terminal) return null
    const result = await cancelBaiduAuthSession(current.id)
    if (!result.success || !result.data || !terminalStatuses.has(result.data.status)) throw new Error(result.message || '取消扫码尚未确认，请稍后重试')
    current.terminal = true
    if (mounted.current) setSession(result.data)
    return result.data
  }
  const close = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const cancelled = await cancelPending()
      if (cancelled?.status === 'authorized') await acceptAuthorized()
      else onClose()
    } catch (reason) { if (mounted.current) setError(getApiErrorMessage(reason, '取消扫码失败，请稍后重试')) }
    finally { if (mounted.current) setBusy(false) }
  }
  const startAuthorization = async () => {
    if (busy || !config) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const cancelled = await cancelPending()
      if (cancelled?.status === 'authorized') { await acceptAuthorized(); return }
      const result = await startBaiduAuthorization()
      if (!result.success || !result.data?.session_id) throw new Error(result.message || '获取扫码二维码失败')
      const next = result.data
      if (!mounted.current) { await cancelBaiduAuthSession(next.session_id); return }
      activeSession.current = { id: next.session_id, terminal: false }
      if (next.mode !== 'baidupcs-go' || !validQrImage(next.qrcode_image)) {
        await cancelPending()
        throw new Error('服务端未返回有效的百度登录二维码，请重新获取')
      }
      setSession(null)
      setAuthorization(next)
    } catch (reason) {
      if (mounted.current) setError(getApiErrorMessage(reason, '获取扫码二维码失败'))
    } finally { if (mounted.current) setBusy(false) }
  }
  const logout = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const result = await revokeBaiduAuthorization()
      if (!result.success || !result.data || result.data.connected !== false) throw new Error(result.message || '退出百度账号失败')
      if (!mounted.current) return
      onStatus({ connected: false, verified: false, available: true, auth_mode: 'baidupcs-go', message: result.data.message })
      onClose()
    } catch (reason) { if (mounted.current) setError(getApiErrorMessage(reason, '退出百度账号失败')) }
    finally { if (mounted.current) setBusy(false) }
  }

  const ended = session && ['expired', 'failed', 'cancelled'].includes(session.status)
  return <QuarkDialog compact title={intent === 'logout' ? '退出百度网盘账号' : intent === 'switch' ? '切换百度网盘账号' : '扫码登录百度网盘'} busy={busy} error={error} onClose={() => void close()}>
    {intent === 'logout' ? <>
      <p className="text-sm leading-7 text-slate-600 dark:text-slate-300">确认退出{nickname ? `「${nickname}」` : '当前百度网盘账号'}？本项目将清除已保存的登录状态，后续可重新扫码登录其他账号。</p>
      <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={() => void close()}>取消</button><button type="button" className="inline-flex items-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700" onClick={() => void logout()}><LogOut className="h-4 w-4" />确认退出</button></div>
    </> : loading ? <p role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />正在准备扫码登录…</p> : <>
      <p className="text-sm leading-7 text-slate-500">使用百度网盘 App 扫码并确认登录，完成后自动展示账号文件。登录状态保存在本项目服务端，刷新页面可恢复。</p>
      {intent === 'switch' && <p className="rounded-md bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">请使用目标百度账号扫码。新账号验证成功后才会替换当前账号，取消扫码会保留当前账号。</p>}
      {!config ? <button type="button" className={quarkButton} onClick={() => void loadConfig()}><RefreshCw className="h-4 w-4" />重新读取配置</button> : !authorization ? <div className="flex flex-col items-center gap-4 py-7"><button type="button" className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-5 py-2.5 text-sm text-white hover:bg-blue-700" onClick={() => void startAuthorization()}><LogIn className="h-4 w-4" />获取登录二维码</button><p className="text-xs text-slate-500">通过 BaiduPCS-Go 连接当前百度账号的网盘目录。</p></div> : <div className="flex flex-col items-center gap-4 py-2">
        <div className="relative rounded-lg bg-white p-3 ring-1 ring-slate-200"><img src={authorization.qrcode_image} alt="百度网盘登录二维码" className={`h-60 w-60 object-contain ${ended ? 'opacity-25' : ''}`} onError={() => setError('登录二维码无法显示，请重新获取')} />{ended && <div className="absolute inset-0 flex items-center justify-center"><span className="rounded bg-white px-3 py-2 text-sm font-medium text-slate-700">{sessionLabels[session.status]}</span></div>}</div>
        <p role="status" className={`text-center text-sm ${ended ? 'text-amber-700 dark:text-amber-400' : 'text-blue-600 dark:text-blue-400'}`}>{session ? sessionLabels[session.status] : '正在读取扫码状态…'}</p>
        {(session?.message || authorization.message) && <p className="text-center text-xs leading-6 text-slate-500">{session?.message || authorization.message}</p>}
        {authorization.expires_at > 0 && <p className="text-xs text-slate-500">二维码有效至 {new Date(authorization.expires_at).toLocaleTimeString('zh-CN', { hour12: false })}</p>}
        {session?.status === 'authorized' ? <button type="button" className={quarkButton} onClick={() => void acceptAuthorized()}><RefreshCw className="h-4 w-4" />重新检查登录结果</button> : <button type="button" className={quarkButton} onClick={() => void startAuthorization()}><RefreshCw className="h-4 w-4" />重新获取二维码</button>}
      </div>}
      {notice && <p role="status" className="text-sm text-amber-700 dark:text-amber-400">{notice}</p>}
      <div className="flex justify-end"><button type="button" className={quarkButton} onClick={() => void close()}>{authorization && !activeSession.current?.terminal ? '取消扫码并关闭' : '关闭'}</button></div>
    </>}
  </QuarkDialog>
}
