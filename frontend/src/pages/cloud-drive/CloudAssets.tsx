import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeftRight, CheckCircle2, ChevronRight, Cloud, Copy, FileText, FolderInput, HardDrive,
  Link2, Loader2, Pencil, RefreshCw, RotateCcw, Search, Ticket, Upload, X,
} from 'lucide-react'
import {
  getCloudAsset, getCloudAssetAvailability, getCloudAssets,
  type CloudAsset, type CloudAssetAnalysis, type CloudAssetAvailability, type CloudAssetStatus, type CloudAssetTarget,
} from '@/api/cloudAssets'
import { getApiErrorMessage } from '@/utils/request'
import { formatBytes } from './QuarkFileManager'
import { quarkButton } from './QuarkTools'
import { CloudAssetCreateDialog, type CloudAssetCreateMode } from './CloudAssetCreateDialog'
import { CloudAssetActionDialog, type CloudAssetAction } from './CloudAssetActionDialog'

const providerNames = { quark: '夸克网盘', baidu: '百度网盘' }
const assetStatuses: Record<CloudAssetStatus, string> = {
  staged: '待同步', importing: '导入中', syncing: '同步中', partial: '部分完成', ready: '已同步', sharing: '分享中',
  deliverable: '可发货', failed: '失败', interrupted: '已中断',
}
const targetStatuses = { pending: '等待同步', uploading: '上传中', uploaded: '已上传', sharing: '创建分享中', shared: '已分享', failed: '失败', unknown: '结果待确认' }
const activeAsset = (asset: CloudAsset) => ['importing', 'syncing', 'sharing'].includes(asset.status) || asset.targets.some(target => ['uploading', 'sharing'].includes(target.status))
const timestamp = (value: string | number) => typeof value === 'number' ? value < 10 ** 12 ? value * 1000 : value : Date.parse(value)
const formatTime = (value?: string | number | null) => {
  if (value == null || value === '') return '—'
  const stamp = timestamp(value)
  return Number.isFinite(stamp) ? new Date(stamp).toLocaleString('zh-CN', { hour12: false }) : '—'
}
const shareLink = (target: CloudAssetTarget) => {
  try {
    const url = new URL(target.share_url || '')
    const expected = target.provider === 'quark' ? 'pan.quark.cn' : 'pan.baidu.com'
    return url.protocol === 'https:' && url.hostname === expected && !url.username && !url.password && ['', '443'].includes(url.port) ? target.share_url : undefined
  } catch { return undefined }
}
const validTargetShare = (target: CloudAssetTarget) => target.status === 'shared' && !!shareLink(target)
  && (target.expires_at == null || target.expires_at === 0 || timestamp(target.expires_at) > Date.now())
const canCreateCard = (asset: CloudAsset) => asset.deliverable && asset.published_version != null && !!asset.delivery_content?.trim()

function StatusBadge({ status, target = false }: { status: string; target?: boolean }) {
  const label = target ? targetStatuses[status as keyof typeof targetStatuses] : assetStatuses[status as CloudAssetStatus]
  const color = ['deliverable', 'ready', 'uploaded', 'shared'].includes(status) ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300'
    : ['failed', 'interrupted'].includes(status) ? 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300'
    : ['partial', 'unknown'].includes(status) ? 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300'
    : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
  return <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded px-2 py-1 text-xs font-medium ${color}`}>{['importing', 'syncing', 'sharing', 'uploading'].includes(status) && <Loader2 className="h-3 w-3 animate-spin" />}{label || status}</span>
}

function AssetAnalysis({ value }: { value: CloudAssetAnalysis | null | undefined }) {
  if (value == null) return <p className="text-sm text-slate-500">尚未分析，可点击「提取与分析」读取文件内容和元数据。</p>
  const extractionLabels: Record<string, string> = { text: '文本已提取', docx: 'Word 文本已提取', 'metadata-only': '已读取文件信息，暂不支持文本提取', 'unreadable-document': '无法解析文档内容' }
  return <div className="space-y-3"><p className="text-xs leading-6 text-slate-500">{value.file_count} 个文件 · 共 {formatBytes(value.total_bytes)} · {value.ai_generated ? '服务端生成内容' : '文件元数据与原文提取'}</p>{value.message && <p className="text-xs leading-6 text-slate-500">{value.message}</p>}{value.files.map((file, index) => <div key={`${file.sha256}:${index}`} className="space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-700"><p className="break-all text-sm font-medium">{file.name}</p><p className="text-xs leading-6 text-slate-500">{formatBytes(file.size)} · {extractionLabels[file.extraction] || file.extraction}{file.truncated ? ' · 仅展示部分原文' : ''}</p>{file.text && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-3 text-xs leading-6 dark:bg-slate-950">{file.text}</pre>}</div>)}</div>
}

export function CloudAssets() {
  const [params, setParams] = useSearchParams()
  const selectedId = params.get('asset') || ''
  const [availability, setAvailability] = useState<CloudAssetAvailability | null>(null)
  const [availabilityLoading, setAvailabilityLoading] = useState(true)
  const [availabilityError, setAvailabilityError] = useState('')
  const [assets, setAssets] = useState<CloudAsset[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')
  const [detail, setDetail] = useState<CloudAsset | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [pollError, setPollError] = useState('')
  const [notice, setNotice] = useState('')
  const [copying, setCopying] = useState(false)
  const [createMode, setCreateMode] = useState<CloudAssetCreateMode | null>(null)
  const [action, setAction] = useState<CloudAssetAction | null>(null)
  const [detailTab, setDetailTab] = useState<'platforms' | 'files' | 'analysis'>('platforms')
  const listVersion = useRef(0)
  const statusVersion = useRef(0)
  const detailVersion = useRef(0)
  const changeVersion = useRef(0)
  const selectedRef = useRef(selectedId)
  selectedRef.current = selectedId

  const loadAvailability = useCallback(async () => {
    const sequence = ++statusVersion.current
    setAvailabilityLoading(true)
    setAvailabilityError('')
    try {
      const result = await getCloudAssetAvailability()
      if (sequence !== statusVersion.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取网盘连接状态失败')
      setAvailability(result.data)
    } catch (reason) { if (sequence === statusVersion.current) setAvailabilityError(getApiErrorMessage(reason, '读取网盘连接状态失败')) }
    finally { if (sequence === statusVersion.current) setAvailabilityLoading(false) }
  }, [])
  const loadList = useCallback(async () => {
    const sequence = ++listVersion.current
    setLoading(true)
    setListError('')
    try {
      const result = await getCloudAssets(page, pageSize)
      if (sequence !== listVersion.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取同步资料失败')
      setAssets(result.data.list)
      setTotal(result.data.total)
    } catch (reason) { if (sequence === listVersion.current) setListError(getApiErrorMessage(reason, '读取同步资料失败')) }
    finally { if (sequence === listVersion.current) setLoading(false) }
  }, [page, pageSize])
  const loadDetail = useCallback(async () => {
    const sequence = ++detailVersion.current
    if (!selectedId) { setDetail(null); setDetailError(''); setDetailLoading(false); return }
    setDetailLoading(true)
    setDetailError('')
    try {
      const result = await getCloudAsset(selectedId)
      if (sequence !== detailVersion.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取资料详情失败')
      setDetail(result.data)
    } catch (reason) { if (sequence === detailVersion.current) setDetailError(getApiErrorMessage(reason, '读取资料详情失败')) }
    finally { if (sequence === detailVersion.current) setDetailLoading(false) }
  }, [selectedId])
  const invalidateAvailability = useCallback(() => { statusVersion.current++ }, [])
  const invalidateList = useCallback(() => { listVersion.current++ }, [])
  const invalidateDetail = useCallback(() => { detailVersion.current++ }, [])
  useEffect(() => { void loadAvailability(); return invalidateAvailability }, [loadAvailability, invalidateAvailability])
  useEffect(() => { setAssets([]); void loadList(); return invalidateList }, [loadList, invalidateList])
  useEffect(() => { setDetail(null); setAction(null); setDetailTab('platforms'); void loadDetail(); return invalidateDetail }, [loadDetail, invalidateDetail])

  const acceptAsset = useCallback((next: CloudAsset) => {
    setAssets(current => current.map(item => item.id === next.id ? next : item))
    if (selectedRef.current === next.id) setDetail(next)
  }, [])
  const activity = [...new Set([...assets.filter(activeAsset).map(item => item.id), ...(detail && activeAsset(detail) ? [detail.id] : [])])].sort().join(',')
  useEffect(() => {
    if (!activity) { setPollError(''); return }
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      if (!active) return
      if (!document.hidden) {
        const currentVersion = changeVersion.current
        const results = await Promise.allSettled(activity.split(',').map(id => getCloudAsset(id)))
        if (!active) return
        if (currentVersion === changeVersion.current) {
          const errors: string[] = []
          results.forEach(result => {
            if (result.status === 'fulfilled' && result.value.success && result.value.data) acceptAsset(result.value.data)
            else errors.push(result.status === 'rejected' ? getApiErrorMessage(result.reason, '读取任务进度失败') : result.value.message || '读取任务进度失败')
          })
          setPollError(errors[0] || '')
        }
      }
      if (active) timer = setTimeout(() => void poll(), 5000)
    }
    timer = setTimeout(() => void poll(), 5000)
    return () => { active = false; clearTimeout(timer) }
  }, [activity, acceptAsset])

  const selectAsset = (id?: string) => { setParams(current => { const next = new URLSearchParams(current); if (id) next.set('asset', id); else next.delete('asset'); return next }); setNotice('') }
  const updated = (next: CloudAsset, message?: string) => {
    changeVersion.current++
    detailVersion.current++
    setDetailLoading(false)
    acceptAsset(next)
    if (message || next.message) setNotice(message || next.message || '')
  }
  const created = (next: CloudAsset, message?: string) => {
    updated(next, message)
    selectAsset(next.id)
    setNotice(message || next.message || `资料已记录，当前状态：${assetStatuses[next.status] || next.status}。`)
    if (page !== 1) setPage(1)
    else void loadList()
  }
  const refresh = () => { void loadList(); void loadAvailability(); if (selectedId) void loadDetail() }
  const connectedCount = availability?.providers.filter(item => item.connected).length || 0
  const copyDelivery = async () => {
    if (!detail?.delivery_content || copying) return
    setCopying(true)
    try {
      const latest = await getCloudAsset(detail.id)
      if (latest.data?.id === detail.id) acceptAsset(latest.data)
      if (!latest.success || !latest.data?.deliverable || !latest.data.delivery_content) throw new Error(latest.message || '已发布分享当前不可发货，请刷新并核对有效期。')
      try { await navigator.clipboard.writeText(latest.data.delivery_content); setNotice('已核验当前发货内容并复制。') }
      catch { setNotice('浏览器未允许复制，请选中下方已更新的文本手动复制。') }
    } catch (reason) { setNotice(getApiErrorMessage(reason, '核验发货文本失败，请先刷新资源详情。')) }
    finally { setCopying(false) }
  }
  const syncAllowed = !!detail && !activeAsset(detail) && detail.files.length > 0 && detail.targets.length > 0 && detail.status !== 'deliverable'
  const shareAllowed = !!detail && !activeAsset(detail) && detail.targets.length > 0 && detail.targets.every(target => ['uploaded', 'shared'].includes(target.status))
  const retryAllowed = !!detail && !activeAsset(detail) && (['failed', 'partial', 'interrupted'].includes(detail.status) || detail.targets.some(target => ['failed', 'unknown'].includes(target.status)))

  return <section aria-label="多网盘同步资料" className="overflow-hidden rounded-lg border border-slate-200 bg-white text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
      <div><h1 className="text-lg font-semibold">同步资料</h1><p className="mt-1 text-xs leading-6 text-slate-500">一份资料管理多个网盘，查看同步、分享与发货卡券。</p></div>
      <div className="flex flex-wrap items-center gap-2"><button type="button" disabled={availabilityLoading || !availability || connectedCount === 0} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50" onClick={() => setCreateMode('upload')}><Upload className="h-4 w-4" />上传资料</button><button type="button" className={quarkButton} disabled={availabilityLoading || !availability || connectedCount === 0} onClick={() => setCreateMode('import')}><FolderInput className="h-4 w-4" />导入已有文件</button><button type="button" className={quarkButton} disabled={availabilityLoading || !availability || connectedCount === 0} onClick={() => setCreateMode('url')}><Link2 className="h-4 w-4" />直链导入</button><button type="button" className={quarkButton} disabled={loading || availabilityLoading} onClick={refresh}><RefreshCw className={`h-4 w-4 ${loading || availabilityLoading ? 'animate-spin' : ''}`} />刷新</button></div>
    </header>
    <div className="flex flex-wrap items-start gap-4 border-b border-slate-100 px-5 py-3 dark:border-slate-800">{availabilityLoading && !availability ? <p role="status" className="flex items-center gap-2 text-xs text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在检查网盘连接…</p> : availability?.providers.map(item => <div key={item.provider} className="flex max-w-md items-start gap-2"><HardDrive className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" /><div className="text-xs leading-6"><div className="flex items-center gap-2"><span className="font-medium">{providerNames[item.provider]}</span><span className={item.connected && item.verified ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'}>{item.connected ? item.verified ? '已连接' : '待验证' : '未授权'}</span><Link to={`/cloud-drive/${item.provider}`} className="text-blue-600 hover:underline">{item.connected ? '管理账号' : '去授权'}</Link></div>{item.message && <p className="text-slate-500">{item.message}</p>}{item.share_notice && <p className="text-amber-700 dark:text-amber-400">{item.share_notice}</p>}</div></div>)}{availabilityError && <p role="alert" className="text-xs text-red-600 dark:text-red-400">{availabilityError}<button type="button" className="ml-2 underline" onClick={() => void loadAvailability()}>重新检查</button></p>}</div>
    {availability && <div aria-label="资料管理限制" className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-slate-100 bg-slate-50/40 px-5 py-2 text-xs leading-6 text-slate-500 dark:border-slate-800 dark:bg-slate-950/20">{typeof availability.limits.max_files === 'number' && <span>单份最多 {availability.limits.max_files} 个文件</span>}{typeof availability.limits.max_upload_bytes === 'number' && <span>单份最大 {formatBytes(availability.limits.max_upload_bytes)}</span>}{typeof availability.limits.scope === 'string' && <span>{availability.limits.scope}</span>}</div>}
    {notice && <p role="status" className="border-b border-slate-100 px-5 py-3 text-sm leading-6 text-blue-700 dark:border-slate-800 dark:text-blue-300">{notice}</p>}
    {pollError && <p role="alert" className="px-5 pt-3 text-xs text-amber-700 dark:text-amber-400">任务进度暂未更新：{pollError}。稍后会重试，也可手动刷新。</p>}
    <div className={`grid min-h-[34rem] ${selectedId ? 'xl:grid-cols-[minmax(0,1fr)_24rem] 2xl:grid-cols-[minmax(0,1fr)_28rem]' : ''}`}>
      <div className="min-w-0 space-y-4 p-5">
        <div className="flex items-center justify-between gap-3 text-sm"><span className="font-medium">资源列表 <span className="ml-1 font-normal text-slate-400">{total} 项</span></span><span className="text-xs text-slate-500">{activity ? '活动任务自动更新' : '进度已保存，刷新可恢复'}</span></div>
        {listError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{listError}<button type="button" className="ml-2 underline" onClick={() => void loadList()}>重试</button></p>}
        <div className="overflow-x-auto rounded-md border border-slate-100 dark:border-slate-800"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-slate-500 dark:bg-slate-800/50"><tr><th className="px-4 py-3 font-medium">资料名称</th><th className="whitespace-nowrap px-3 py-3 font-medium">网盘</th><th className="px-3 py-3 font-medium">状态</th><th className="whitespace-nowrap px-3 py-3 font-medium">更新时间</th><th className="w-8 px-3 py-3"><span className="sr-only">详情</span></th></tr></thead><tbody className="divide-y divide-slate-100 dark:divide-slate-800">{loading ? <tr><td colSpan={5} className="py-16 text-center text-slate-500"><Loader2 className="mx-auto mb-3 h-5 w-5 animate-spin text-blue-600" />读取同步资料…</td></tr> : assets.length ? assets.map(asset => <tr key={asset.id} className={selectedId === asset.id ? 'bg-blue-50/60 dark:bg-blue-950/30' : 'hover:bg-slate-50 dark:hover:bg-slate-800/30'}><td className="min-w-52 px-4 py-3.5"><button type="button" className="flex items-start gap-3 text-left hover:text-blue-600" onClick={() => selectAsset(asset.id)}><FileText className="mt-0.5 h-5 w-5 shrink-0 text-blue-500" /><span className="min-w-0"><span className="block break-all font-medium">{asset.name}</span><span className="mt-1 block text-xs text-slate-400">版本 {asset.version} · {asset.files.length} 个文件 · {formatBytes(asset.files.reduce((sum, file) => sum + file.size, 0))}</span></span></button></td><td className="px-3 py-3"><div className="space-y-1">{asset.targets.map(target => <span key={target.provider} className="block whitespace-nowrap text-xs text-slate-500">{providerNames[target.provider]} · {targetStatuses[target.status]}</span>)}</div></td><td className="px-3 py-3"><StatusBadge status={asset.status} /></td><td className="whitespace-nowrap px-3 py-3 text-xs text-slate-500">{formatTime(asset.updated_at)}</td><td className="px-3 py-3"><button type="button" aria-label={`查看 ${asset.name} 详情`} className="p-1 text-slate-400 hover:text-blue-600" onClick={() => selectAsset(asset.id)}><ChevronRight className="h-4 w-4" /></button></td></tr>) : !listError && <tr><td colSpan={5} className="py-16 text-center"><Cloud className="mx-auto mb-4 h-10 w-10 text-slate-300" /><p className="text-sm text-slate-500">暂无同步资料</p><p className="mt-2 text-xs leading-6 text-slate-400">上传本地文件，或从已授权网盘导入。</p></td></tr>}</tbody></table></div>
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500"><label className="flex items-center gap-2 text-xs">每页<select className="rounded border border-slate-200 bg-transparent px-2 py-1 dark:border-slate-700" value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPage(1) }}>{[10, 20, 50].map(value => <option key={value} value={value}>{value} 项</option>)}</select></label><div className="flex items-center gap-2"><button type="button" className={quarkButton} disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span className="text-xs">第 {page} 页</span><button type="button" className={quarkButton} disabled={loading || page * pageSize >= total} onClick={() => setPage(value => value + 1)}>下一页</button></div></div>
      </div>
      {selectedId && <aside aria-label="同步资料详情" className="min-w-0 space-y-5 border-t border-slate-200 bg-slate-50/30 p-5 xl:border-l xl:border-t-0 dark:border-slate-700 dark:bg-slate-950/10">
        <div className="flex items-start justify-between gap-3"><h2 className="text-sm font-semibold">资料详情</h2><div className="flex items-center gap-1"><button type="button" aria-label="刷新资料详情" disabled={detailLoading} className="p-1 text-slate-400 hover:text-blue-600" onClick={() => void loadDetail()}><RefreshCw className={`h-4 w-4 ${detailLoading ? 'animate-spin' : ''}`} /></button><button type="button" aria-label="关闭资料详情" className="p-1 text-slate-400 hover:text-blue-600" onClick={() => selectAsset()}><X className="h-4 w-4" /></button></div></div>
        {detailError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{detailError}</p>}
        {detailLoading && !detail ? <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />读取资源详情…</p> : detail && <>
          <div className="space-y-2"><div className="flex items-start justify-between gap-3"><h3 className="min-w-0 break-all font-semibold">{detail.name}</h3><button type="button" aria-label="修改资料名称" disabled={activeAsset(detail)} className="p-1 text-slate-400 hover:text-blue-600 disabled:opacity-40" onClick={() => setAction('rename')}><Pencil className="h-4 w-4" /></button></div><div className="flex flex-wrap items-center gap-2"><StatusBadge status={detail.status} /><span className="text-xs text-slate-500">版本 {detail.version} · {detail.files.length} 个文件</span></div><p className="text-xs text-slate-400">更新于 {formatTime(detail.updated_at)}</p>{detail.message && <p className="text-xs leading-6 text-slate-500">{detail.message}</p>}{detail.published_version != null && <p className={`text-xs leading-6 ${detail.deliverable ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'}`}>已发布版本 {detail.published_version}{detail.deliverable ? ' · 可继续发货' : ' · 分享需核验'}{detail.published_version !== detail.version ? '，当前新版本尚未发布。' : '。'}</p>}</div>
          <div className="flex flex-wrap gap-2"><button type="button" className={quarkButton} disabled={!syncAllowed} onClick={() => setAction('sync')}><ArrowLeftRight className="h-4 w-4" />同步网盘</button><button type="button" className={quarkButton} disabled={activeAsset(detail) || !availability} onClick={() => setCreateMode('version')}><Upload className="h-4 w-4" />上传新版本</button><button type="button" className={quarkButton} disabled={!shareAllowed} onClick={() => setAction('shares')}><Link2 className="h-4 w-4" />生成分享</button><button type="button" className={quarkButton} disabled={!retryAllowed} onClick={() => setAction('retry')}><RotateCcw className="h-4 w-4" />恢复任务</button></div>
          {activeAsset(detail) && <p role="status" className="flex items-center gap-2 text-xs leading-6 text-blue-600"><Loader2 className="h-3.5 w-3.5 animate-spin" />任务执行中，可离开页面，进度将保存在服务端。</p>}
          <nav aria-label="资料详情分类" className="flex gap-4 border-b border-slate-200 text-sm dark:border-slate-700">{([['platforms', '网盘状态'], ['files', '资料文件'], ['analysis', '内容分析']] as const).map(([value, label]) => <button type="button" key={value} aria-current={detailTab === value ? 'page' : undefined} className={`border-b-2 pb-2 ${detailTab === value ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500'}`} onClick={() => setDetailTab(value)}>{label}</button>)}</nav>
          {detailTab === 'platforms' && <div className="space-y-3">{detail.targets.map(target => <div key={target.provider} className="space-y-2 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900"><div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{providerNames[target.provider]}</span><StatusBadge status={target.status} target /></div>{target.message && <p className={`break-all text-xs leading-6 ${['failed', 'unknown'].includes(target.status) ? 'text-amber-700 dark:text-amber-400' : 'text-slate-500'}`}>{target.message}</p>}{shareLink(target) && <div className="space-y-1 text-xs leading-6"><a href={shareLink(target)} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="block break-all text-blue-600 underline">{target.share_url}</a>{target.passcode && <p>提取码：{target.passcode}</p>}<p className="text-slate-500">{target.expires_at == null || target.expires_at === 0 ? '未设置到期时间' : `有效至 ${formatTime(target.expires_at)}`}</p>{!validTargetShare(target) && <p className="text-amber-700 dark:text-amber-400">分享当前不可用于发货，请核对状态或重新生成。</p>}</div>}{target.files?.length > 0 && <details className="text-xs"><summary className="cursor-pointer text-slate-500">云端文件记录 · {target.files.length} 项</summary><ul className="mt-2 max-h-48 space-y-3 overflow-auto text-xs leading-6">{target.files.map((file, index) => <li key={`${file.sha256}:${index}`} className="space-y-1 break-all"><p className="font-medium">{file.name}<span className="ml-2 font-normal text-slate-500">{formatBytes(file.size)}</span></p>{file.path && <p className="text-slate-500">{file.path}</p>}</li>)}</ul></details>}</div>)}{detail.targets.length === 0 && <p className="text-sm text-slate-500">尚无网盘目标，请核对资源创建结果。</p>}</div>}
          {detailTab === 'files' && <div className="space-y-3">{detail.files.map((file, index) => <div key={`${file.sha256}-${index}`} className="space-y-1 rounded-md border border-slate-200 p-3 dark:border-slate-700"><p className="break-all text-sm font-medium">{file.name}</p><p className="text-xs text-slate-500">{formatBytes(file.size)}</p><details className="text-xs text-slate-500"><summary className="cursor-pointer">文件校验信息</summary><p className="mt-1 break-all font-mono">SHA-256：{file.sha256 || '未返回'}</p></details></div>)}{detail.files.length === 0 && <p className="text-sm text-slate-500">暂无文件记录。</p>}</div>}
          {detailTab === 'analysis' && <div className="space-y-3"><button type="button" className={quarkButton} disabled={activeAsset(detail) || !detail.files.length} onClick={() => setAction('analyze')}><Search className="h-4 w-4" />提取与分析</button><p className="text-xs leading-6 text-slate-500">展示文件文本和元数据的实际提取结果。</p><AssetAnalysis value={detail.analysis} /></div>}
          <div className="space-y-3 border-t border-slate-200 pt-4 dark:border-slate-700"><div className="flex items-center justify-between gap-3"><h3 className="flex items-center gap-2 text-sm font-semibold"><Ticket className="h-4 w-4 text-slate-400" />发货与卡券</h3>{detail.card_id && <Link to="/cards" className="inline-flex items-center gap-1 text-xs text-blue-600"><CheckCircle2 className="h-3.5 w-3.5" />查看卡券</Link>}</div>{detail.published_version != null && <p className="text-xs leading-6 text-slate-500">以下发货内容来自已发布版本 {detail.published_version}。</p>}{detail.delivery_content ? <><textarea aria-label="同步资料发货文本" readOnly className="min-h-32 w-full resize-y rounded-md border border-slate-200 bg-white p-3 text-xs leading-6 outline-none dark:border-slate-700 dark:bg-slate-900" value={detail.delivery_content} /><button type="button" className={quarkButton} disabled={copying} onClick={() => void copyDelivery()}>{copying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Copy className="h-4 w-4" />}复制发货文本</button></> : <p className="text-xs leading-6 text-slate-500">全部指定平台分享成功后，才会生成多网盘发货文本。</p>}{detail.card_id && <p className="break-all text-xs leading-6 text-emerald-700 dark:text-emerald-400">已创建关联卡券：{detail.card_id}</p>}<><button type="button" disabled={!canCreateCard(detail) || activeAsset(detail)} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-40" onClick={() => setAction('card')}><Ticket className="h-4 w-4" />{detail.card_id ? '更新关联卡券' : '创建关联卡券'}</button>{!canCreateCard(detail) && <p className="text-xs leading-6 text-slate-500">已发布版本的全部分享均有效后，才能创建或更新关联卡券。</p>}</></div>
        </>}
      </aside>}
    </div>
    {createMode && availability && <CloudAssetCreateDialog mode={createMode} availability={availability} asset={createMode === 'version' ? detail || undefined : undefined} onCreated={createMode === 'version' ? updated : created} onClose={() => setCreateMode(null)} />}
    {action && detail && <CloudAssetActionDialog key={`${detail.id}:${action}`} action={action} asset={detail} availability={availability} onUpdated={updated} onClose={() => setAction(null)} />}
  </section>
}
