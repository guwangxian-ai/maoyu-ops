import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import {
  ChevronRight, Copy, Download, ExternalLink, File, FileText, Folder, FolderPlus, Grid2X2, Image,
  Link2, List, Loader2, MoreHorizontal, Move, Music, Pencil, PlaySquare, RefreshCw, Search, Sparkles, Trash2, Upload, X,
} from 'lucide-react'
import {
  cancelBaiduShare, createBaiduShare, getBaiduCapabilities, getBaiduFiles, getBaiduShareDetail, getBaiduShares,
  type BaiduCapabilities, type BaiduCategory, type BaiduFile, type BaiduShare,
} from '@/api/baiduDrive'
import { getApiErrorMessage } from '@/utils/request'
import { formatBytes } from './QuarkFileManager'
import { QuarkDialog, quarkButton, quarkInput } from './QuarkTools'
import { BaiduOperationDialog, type BaiduTool } from './BaiduOperationDialog'

const periods = [[1, '1 天'], [7, '7 天'], [30, '30 天'], [0, '永久有效']] as const
const rootPath = '/'
const categories = [
  ['all', '全部文件', Folder], ['image', '图片', Image], ['video', '视频', PlaySquare], ['audio', '音频', Music],
  ['doc', '文档', FileText], ['app', '应用', File], ['other', '其他', File], ['folder', '文件夹', Folder],
] as const
const rowActions = [['file-meta', '文件详情', FileText], ['download', '下载', Download], ['copy', '复制', Copy], ['move', '移动', Move], ['rename', '重命名', Pencil], ['delete', '删除', Trash2]] as const
const formatTime = (value?: string | number | null) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
const shareText = (share: BaiduShare) => `${share.share_url}${share.passcode ? `\n提取码：${share.passcode}` : ''}`
const shareUrl = (value: string) => {
  try {
    const url = new URL(value)
    if (url.protocol === 'https:' && url.hostname === 'pan.baidu.com' && !url.username && !url.password && ['', '443'].includes(url.port)) return value
  } catch { /* Invalid links are displayed as plain text. */ }
  return undefined
}
const shareStatus = (share: BaiduShare) => share.status === 'active' && share.expires_at != null && share.expires_at > 0 && share.expires_at <= Date.now() ? 'expired' : share.status
const shareStatusLabels: Record<BaiduShare['status'], string> = { active: '有效', expired: '已过期', cancelled: '已取消', unavailable: '待检查' }

function FileIcon({ file, large = false }: { file: BaiduFile; large?: boolean }) {
  const size = large ? 'h-11 w-11' : 'h-6 w-6'
  if (file.isdir) return <Folder className={`${size} shrink-0 fill-amber-400 text-amber-400`} strokeWidth={1.3} />
  if (file.category === 3) return <Image className={`${size} shrink-0 text-emerald-500`} />
  if (file.category === 1) return <PlaySquare className={`${size} shrink-0 text-violet-500`} />
  if (file.category === 2) return <Music className={`${size} shrink-0 text-pink-500`} />
  if (file.category === 4) return <FileText className={`${size} shrink-0 text-blue-500`} />
  return <File className={`${size} shrink-0 text-slate-400`} />
}

export function BaiduFileManager({ accountControl, statusNotice }: { accountControl: ReactNode; statusNotice?: ReactNode }) {
  const [tab, setTab] = useState<'files' | 'shares'>('files')
  const [dir, setDir] = useState(rootPath)
  const [category, setCategory] = useState<BaiduCategory>('all')
  const [searchInput, setSearchInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [files, setFiles] = useState<BaiduFile[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [filesError, setFilesError] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [view, setView] = useState<'list' | 'grid'>('list')
  const [sort, setSort] = useState<'updated' | 'name' | 'size'>('updated')
  const [semanticLabel, setSemanticLabel] = useState('')
  const [shares, setShares] = useState<BaiduShare[] | null>(null)
  const [sharesLoading, setSharesLoading] = useState(false)
  const [sharesError, setSharesError] = useState('')
  const [sharePage, setSharePage] = useState(1)
  const [sharesHasMore, setSharesHasMore] = useState(false)
  const [checkingShares, setCheckingShares] = useState<string[]>([])
  const [shareDetailErrors, setShareDetailErrors] = useState<Record<string, string>>({})
  const [latestShare, setLatestShare] = useState<BaiduShare | null>(null)
  const [shareDraft, setShareDraft] = useState<{ fsids: string[]; paths: string[]; title: string } | null>(null)
  const [period, setPeriod] = useState<0 | 1 | 7 | 30>(7)
  const [sharePassword, setSharePassword] = useState('')
  const [shareBusy, setShareBusy] = useState(false)
  const [shareError, setShareError] = useState('')
  const [cancelDraft, setCancelDraft] = useState<BaiduShare | null>(null)
  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const [notice, setNotice] = useState('')
  const [copyError, setCopyError] = useState('')
  const [toolDraft, setToolDraft] = useState<{ tool: BaiduTool; chosen: BaiduFile[] } | null>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const [fileMenu, setFileMenu] = useState<{ file: BaiduFile; x: number; y: number } | null>(null)
  const [capabilities, setCapabilities] = useState<BaiduCapabilities | null>(null)
  const [capabilityError, setCapabilityError] = useState('')
  const moreRef = useRef<HTMLDivElement>(null)
  const fileMenuRef = useRef<HTMLDivElement>(null)
  const fileVersion = useRef(0)
  const shareVersion = useRef(0)
  const shareDetailRequests = useRef(new Set<string>())

  const loadFiles = useCallback(async () => {
    const version = ++fileVersion.current
    setLoading(true)
    setFilesError('')
    setFiles(null)
    setSelected([])
    setSemanticLabel('')
    try {
      const response = await getBaiduFiles({ dir, keyword, page, page_size: 100, category })
      if (version !== fileVersion.current) return
      if (!response.success || !response.data) throw new Error(response.message || '读取百度文件失败')
      setFiles(response.data.files)
      setHasMore(response.data.has_more)
    } catch (reason) {
      if (version === fileVersion.current) setFilesError(getApiErrorMessage(reason, '读取百度文件失败'))
    } finally { if (version === fileVersion.current) setLoading(false) }
  }, [dir, keyword, page, category])

  const loadShares = useCallback(async () => {
    const version = ++shareVersion.current
    setSharesLoading(true)
    setSharesError('')
    setShares(null)
    try {
      const response = await getBaiduShares(sharePage)
      if (version !== shareVersion.current) return
      if (!response.success || !response.data) throw new Error(response.message || '读取分享记录失败')
      setShares(response.data.shares)
      setSharesHasMore(response.data.has_more)
    } catch (reason) {
      if (version === shareVersion.current) setSharesError(getApiErrorMessage(reason, '读取分享记录失败'))
    } finally { if (version === shareVersion.current) setSharesLoading(false) }
  }, [sharePage])

  const invalidateFiles = useCallback(() => { fileVersion.current++ }, [])
  const invalidateShares = useCallback(() => { shareVersion.current++ }, [])
  useEffect(() => { void loadFiles(); return invalidateFiles }, [loadFiles, invalidateFiles])
  useEffect(() => { if (tab === 'shares') void loadShares(); return invalidateShares }, [tab, loadShares, invalidateShares])
  useEffect(() => {
    let active = true
    void getBaiduCapabilities().then(result => {
      if (!active) return
      if (!result.success || !result.data) throw new Error(result.message || '读取百度功能支持情况失败')
      setCapabilities(result.data)
    }).catch(reason => { if (active) setCapabilityError(getApiErrorMessage(reason, '读取百度功能支持情况失败')) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!moreOpen && !fileMenu) return
    const close = (event: MouseEvent) => {
      if (!moreRef.current?.contains(event.target as Node)) setMoreOpen(false)
      if (!fileMenuRef.current?.contains(event.target as Node)) setFileMenu(null)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setMoreOpen(false); setFileMenu(null) } }
    const scroll = () => setFileMenu(null)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', escape)
    window.addEventListener('scroll', scroll, true)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', escape); window.removeEventListener('scroll', scroll, true) }
  }, [moreOpen, fileMenu])

  const chosen = (files || []).filter(file => selected.includes(file.fs_id))
  const unsupported = (tool: BaiduTool | 'share' | 'cancel-share', items: BaiduFile[] = chosen) => {
    if (tool === 'download' && items.some(file => file.isdir)) return capabilities?.unsupported['directory-download'] || '暂不支持直接下载文件夹，请先打包为 ZIP 后下载'
    if (tool === 'semantic-search') return '当前百度接入暂不提供语义搜索'
    if (!capabilities) return '正在读取功能支持情况，请稍后重试'
    return capabilities.unsupported[tool] || (!capabilities.operations.includes(tool) ? '当前百度接口暂不支持该操作' : undefined)
  }
  const openTool = (tool: BaiduTool, items = chosen) => {
    if (unsupported(tool, items)) { setNotice(unsupported(tool, items) || '当前百度接口不支持该操作'); return }
    setMoreOpen(false)
    setFileMenu(null)
    setToolDraft({ tool, chosen: items })
  }
  const navigate = (target: string) => {
    const nextDir = target === '/' ? rootPath : target
    if (nextDir === dir && !keyword && page === 1 && category === 'all') void loadFiles()
    setDir(nextDir)
    setKeyword('')
    setSearchInput('')
    setPage(1)
    setCategory('all')
    setSemanticLabel('')
  }
  const openShare = (items: BaiduFile[]) => {
    if (items.length === 0) return
    if (unsupported('share', items)) { setNotice(unsupported('share', items) || '当前百度接口暂不支持分享'); return }
    setFileMenu(null)
    setShareError('')
    setPeriod(7)
    setSharePassword('')
    setShareDraft({ fsids: items.map(file => file.fs_id), paths: items.map(file => file.path), title: (items.length === 1 ? items[0].filename : `${items[0].filename} 等 ${items.length} 项`).slice(0, 100) })
  }
  const submitShare = async () => {
    if (!shareDraft || shareBusy) return
    setShareError('')
    if (sharePassword.trim() && !/^[A-Za-z0-9]{4}$/.test(sharePassword.trim())) { setShareError('提取码需为 4 位字母或数字，留空由百度自动生成。'); return }
    setShareBusy(true)
    setNotice('')
    try {
      const result = await createBaiduShare({ ...shareDraft, title: shareDraft.title.trim(), period, ...(sharePassword.trim() ? { password: sharePassword.trim() } : {}), confirmed: true })
      if (!result.success || !result.data?.share?.share_url || !shareUrl(result.data.share.share_url)) throw new Error(result.message || '百度未返回有效分享链接，请到网盘核验此次操作')
      setLatestShare(result.data.share)
      setShareDraft(null)
      setSelected([])
      setNotice(result.data.message || '真实分享已创建，可以复制链接和提取码用于发货。')
      setSharePage(1)
      if (tab === 'shares' && sharePage === 1) void loadShares()
      else setTab('shares')
    } catch (reason) {
      setShareError(`${getApiErrorMessage(reason, '创建分享未完成')}。若请求超时，请先在百度网盘核对分享结果后再重试。`)
    } finally { setShareBusy(false) }
  }
  const copyShare = async (share: BaiduShare) => {
    setCopyError('')
    if (shareStatus(share) !== 'active' || !shareUrl(share.share_url)) { setCopyError('此分享当前不可用于发货，请刷新分享状态。'); return }
    try { await navigator.clipboard.writeText(shareText(share)); setNotice('分享发货文本已复制，可粘贴到发货消息中。') }
    catch { setCopyError('浏览器未允许复制，请选中完整链接和提取码复制。') }
  }
  const checkShare = async (share: BaiduShare) => {
    if (shareDetailRequests.current.has(share.id)) return
    const version = shareVersion.current
    shareDetailRequests.current.add(share.id)
    setCheckingShares(current => [...current, share.id])
    setShareDetailErrors(current => ({ ...current, [share.id]: '' }))
    try {
      const response = await getBaiduShareDetail(share.id)
      if (version !== shareVersion.current) return
      const detail = response.data?.share
      if (!response.success || !detail || detail.id !== share.id || detail.status !== 'active' || !shareUrl(detail.share_url) || typeof detail.passcode !== 'string') throw new Error(response.message || '百度未返回有效的分享详情，请稍后重试')
      const update = { share_url: detail.share_url, passcode: detail.passcode, status: detail.status }
      setShares(current => current?.map(item => item.id === share.id ? { ...item, ...update } : item) || null)
      setLatestShare(current => current?.id === share.id ? { ...current, ...update } : current)
    } catch (reason) {
      if (version === shareVersion.current) setShareDetailErrors(current => ({ ...current, [share.id]: getApiErrorMessage(reason, '读取分享详情失败') }))
    } finally {
      shareDetailRequests.current.delete(share.id)
      setCheckingShares(current => current.filter(id => id !== share.id))
    }
  }
  const submitCancelShare = async () => {
    if (!cancelDraft || cancelBusy) return
    setCancelBusy(true)
    setCancelError('')
    try {
      const response = await cancelBaiduShare(cancelDraft.id)
      if (!response.success || !response.data?.cancelled || response.data.share_id !== cancelDraft.id) throw new Error(response.message || '百度未确认取消分享，请刷新核验')
      const id = cancelDraft.id
      setShares(current => current?.map(share => share.id === id ? { ...share, status: 'cancelled' } : share) || null)
      setLatestShare(current => current?.id === id ? { ...current, status: 'cancelled' } : current)
      setCancelDraft(null)
      setNotice('百度已确认取消分享，原链接不再可用于发货。')
      void loadShares()
    } catch (reason) { setCancelError(`${getApiErrorMessage(reason, '取消分享失败')}。若请求超时，请先刷新分享列表核对状态。`) }
    finally { setCancelBusy(false) }
  }
  const operationChanged = (message: string) => { setNotice(message); void loadFiles() }
  const showSemantic = (resultFiles: BaiduFile[], query: string) => {
    fileVersion.current++
    setTab('files')
    setFiles(resultFiles)
    setLoading(false)
    setFilesError('')
    setSelected([])
    setSemanticLabel(query)
    setHasMore(false)
  }
  const sortedFiles = [...(files || [])].sort((a, b) => {
    if (a.isdir !== b.isdir) return a.isdir ? -1 : 1
    if (sort === 'name') return a.filename.localeCompare(b.filename, 'zh-CN')
    if (sort === 'size') return b.size - a.size
    return (b.updated_at || 0) - (a.updated_at || 0)
  })
  const allSelected = sortedFiles.length > 0 && sortedFiles.every(file => selected.includes(file.fs_id))
  const toggle = (file: BaiduFile, checked: boolean) => setSelected(current => checked ? [...new Set([...current, file.fs_id])].slice(0, 100) : current.filter(id => id !== file.fs_id))
  const showFileMenu = (file: BaiduFile, element: HTMLButtonElement) => {
    const rect = element.getBoundingClientRect()
    setFileMenu({ file, x: Math.max(8, Math.min(rect.right - 176, window.innerWidth - 184)), y: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 300)) })
  }
  const parts = dir.split('/').filter(Boolean)
  const shareCard = (share: BaiduShare, latest = false) => {
    const state = shareStatus(share)
    const url = shareUrl(share.share_url)
    const checking = checkingShares.includes(share.id)
    return <div key={share.id} className={`space-y-3 rounded-lg border p-4 ${latest ? 'border-blue-200 bg-blue-50/50 dark:border-blue-900 dark:bg-blue-950/20' : 'border-slate-200 dark:border-slate-700'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0"><h3 className="break-all text-sm font-medium">{latest ? '刚创建的分享：' : ''}{share.title || '未命名分享'}</h3><div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500"><span>{share.created_at ? `创建于 ${formatTime(share.created_at)}` : '创建时间：百度未提供'}</span><span className={state === 'active' ? 'text-green-700 dark:text-green-400' : 'text-amber-700 dark:text-amber-400'}>{shareStatusLabels[state]}</span></div></div>
        <div className="flex flex-wrap gap-2">{!latest && url && state !== 'cancelled' && <button type="button" className={quarkButton} disabled={checking} onClick={() => void checkShare(share)}>{checking ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}{checking ? '正在读取…' : state === 'unavailable' ? '读取提取码' : '检查分享'}</button>}<button type="button" className={quarkButton} disabled={checking || state !== 'active' || !url} onClick={() => void copyShare(share)}><Copy className="h-4 w-4" />复制发货文本</button><button type="button" className={`${quarkButton} text-red-600`} title={unsupported('cancel-share')} disabled={checking || state === 'cancelled' || !!unsupported('cancel-share')} onClick={() => { setCancelDraft(share); setCancelError('') }}><Trash2 className="h-4 w-4" />取消分享</button></div>
      </div>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="flex items-center gap-1.5 break-all text-sm text-blue-600 underline dark:text-blue-400">{share.share_url}<ExternalLink className="h-3.5 w-3.5 shrink-0" /></a> : <p className="break-all text-sm text-slate-500">{share.share_url || '百度未返回分享链接'}</p>}
      {share.passcode && <p className="text-sm text-slate-600 dark:text-slate-300">提取码：{share.passcode}</p>}
      {shareDetailErrors[share.id] && <p role="alert" className="text-xs leading-6 text-red-600 dark:text-red-400">{shareDetailErrors[share.id]}</p>}
      <p className="text-xs leading-6 text-slate-500">{share.expires_at != null && share.expires_at > 0 ? `有效至 ${formatTime(share.expires_at)}` : share.period === 0 ? '永久有效' : periods.some(([value]) => value === share.period) ? `${periods.find(([value]) => value === share.period)?.[1]} · 百度未返回到期时间` : '有效期：百度未提供'}</p>
    </div>
  }

  return <div className="grid min-h-[38rem] md:grid-cols-[10rem_minmax(0,1fr)] lg:grid-cols-[11rem_minmax(0,1fr)]">
    <aside className="flex flex-col border-b border-slate-200 bg-slate-50/40 p-3 md:border-b-0 md:border-r dark:border-slate-700 dark:bg-slate-950/20">
      <nav aria-label="百度网盘文件导航" className="flex flex-wrap gap-1 md:flex-col">{categories.map(([value, label, Icon]) => <button type="button" key={value} aria-current={tab === 'files' && category === value ? 'page' : undefined} onClick={() => { setTab('files'); setCategory(value); setPage(1); if (semanticLabel && category === value && page === 1) void loadFiles() }} className={`flex items-center gap-3 rounded-md px-3 py-3 text-left text-sm transition-colors ${tab === 'files' && category === value ? 'bg-blue-50 font-medium text-blue-700 dark:bg-blue-950/50 dark:text-blue-300' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'}`}><Icon className="h-4 w-4 shrink-0" />{label}</button>)}<div className="my-3 hidden border-t border-slate-200 md:block dark:border-slate-700" /><button type="button" aria-current={tab === 'shares' ? 'page' : undefined} onClick={() => setTab('shares')} className={`flex items-center gap-3 rounded-md px-3 py-3 text-left text-sm transition-colors ${tab === 'shares' ? 'bg-blue-50 font-medium text-blue-700 dark:bg-blue-950/50 dark:text-blue-300' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'}`}><Link2 className="h-4 w-4" />我的分享</button><button type="button" disabled={!!unsupported('share-transfer')} title={unsupported('share-transfer')} onClick={() => openTool('share-transfer', [])} className="flex items-center gap-3 rounded-md px-3 py-3 text-left text-sm text-slate-600 transition-colors hover:bg-slate-100 disabled:opacity-40 dark:text-slate-400 dark:hover:bg-slate-800"><ExternalLink className="h-4 w-4" />分享转存</button></nav>
      <p className="mt-auto hidden px-3 pt-12 text-xs leading-6 text-slate-400 md:block">管理当前百度账号目录<br />分类显示当前目录文件。</p>
    </aside>
    <section className="min-w-0 space-y-4 p-4 sm:p-5 lg:p-6">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 border-b border-slate-100 pb-3 dark:border-slate-800">
        {tab === 'files' ? <div className="flex min-w-0 flex-wrap items-center gap-2">
          <button type="button" className="inline-flex h-10 items-center gap-2 rounded-md bg-blue-600 px-4 text-sm text-white hover:bg-blue-700 disabled:opacity-50" title={unsupported('upload')} disabled={!!unsupported('upload')} onClick={() => openTool('upload', [])}><Upload className="h-4 w-4" />上传</button>
          <button type="button" className={`${quarkButton} h-10`} title={unsupported('create-folder')} disabled={!!unsupported('create-folder')} onClick={() => openTool('create-folder', [])}><FolderPlus className="h-4 w-4" />新建文件夹</button>
          <button type="button" className={`${quarkButton} h-10`} title={unsupported('share')} disabled={selected.length === 0 || loading || !!unsupported('share')} onClick={() => openShare(chosen)}><Link2 className="h-4 w-4" />分享</button>
          <div ref={moreRef} className="relative"><button type="button" className={`${quarkButton} h-10`} aria-label="更多百度网盘操作" aria-haspopup="menu" aria-expanded={moreOpen} onClick={() => setMoreOpen(value => !value)}><MoreHorizontal className="h-4 w-4" />更多</button>{moreOpen && <div role="menu" className="absolute left-0 top-full z-30 mt-1 w-48 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">
            {rowActions.map(([tool, label, Icon]) => <button type="button" role="menuitem" key={tool} title={unsupported(tool)} disabled={selected.length === 0 || ((tool === 'rename' || tool === 'file-meta' || tool === 'download') && selected.length !== 1) || !!unsupported(tool)} className={`flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-40 dark:hover:bg-slate-700 ${tool === 'delete' ? 'text-red-600' : ''}`} onClick={() => openTool(tool)}><Icon className="h-4 w-4" />{label}</button>)}
            <div className="my-1 border-t border-slate-100 dark:border-slate-700" />{([['share-transfer', '分享转存', ExternalLink], ['upload-text', '上传文本', FileText], ['semantic-search', '语义搜索', Sparkles], ['upload-url', '从链接上传', ExternalLink]] as const).map(([tool, label, Icon]) => <button type="button" role="menuitem" key={tool} title={unsupported(tool)} disabled={!!unsupported(tool)} className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-40 dark:hover:bg-slate-700" onClick={() => openTool(tool, [])}><Icon className="h-4 w-4" />{label}</button>)}
          </div>}</div>
          <form className="relative ml-auto min-w-32 max-w-64 flex-1" onSubmit={event => { event.preventDefault(); const next = searchInput.trim(); setPage(1); if (next === keyword && page === 1) void loadFiles(); else setKeyword(next) }}><button type="submit" aria-label="搜索百度网盘文件" disabled={loading} className="absolute inset-y-0 left-0 flex w-9 items-center justify-center text-slate-400 hover:text-blue-600 disabled:opacity-50"><Search className="h-4 w-4" /></button><input aria-label="搜索百度网盘文件" className={`${quarkInput} h-10 pl-9 pr-9`} value={searchInput} maxLength={100} placeholder="搜索当前目录及子目录" onChange={event => setSearchInput(event.target.value)} />{(searchInput || keyword) && <button type="button" aria-label="清除搜索" className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-slate-400 hover:text-blue-600" onClick={() => { setSearchInput(''); setKeyword(''); setPage(1); if (!keyword && page === 1) void loadFiles() }}><X className="h-4 w-4" /></button>}</form>
          <button type="button" className={`${quarkButton} h-10 px-2.5`} aria-label="刷新百度文件" title="刷新文件" disabled={loading} onClick={() => void loadFiles()}><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /></button>
          <div className="flex rounded-md border border-slate-200 p-0.5 dark:border-slate-700">{([['list', List, '列表视图'], ['grid', Grid2X2, '网格视图']] as const).map(([value, Icon, label]) => <button type="button" key={value} aria-label={label} aria-pressed={view === value} title={label} onClick={() => setView(value)} className={`rounded p-2 ${view === value ? 'bg-blue-50 text-blue-600 dark:bg-blue-950/40' : 'text-slate-400 hover:text-blue-600'}`}><Icon className="h-4 w-4" /></button>)}</div>
        </div> : <div className="flex min-h-10 flex-wrap items-center gap-3"><h2 className="font-semibold">我的分享</h2><button type="button" className={quarkButton} disabled={sharesLoading} onClick={() => void loadShares()}><RefreshCw className={`h-4 w-4 ${sharesLoading ? 'animate-spin' : ''}`} />刷新分享</button></div>}
        <div className="justify-self-end">{accountControl}</div>
      </div>
      {statusNotice}
      {notice && <p role="status" className="text-sm leading-6 text-blue-700 dark:text-blue-400">{notice}</p>}
      {copyError && <p role="alert" className="text-sm text-amber-700 dark:text-amber-400">{copyError}</p>}
      {tab === 'files' ? <>
        <div className="flex flex-wrap items-center justify-between gap-3"><nav aria-label="百度网盘目录路径" className="flex min-w-0 flex-wrap items-center gap-1 text-sm"><button type="button" disabled={dir === rootPath && !semanticLabel} className="text-blue-600 disabled:text-slate-700 dark:disabled:text-slate-200" onClick={() => navigate(rootPath)}>全部文件</button>{parts.map((part, index) => <span key={index} className="inline-flex items-center gap-1"><ChevronRight className="h-4 w-4 text-slate-400" /><button type="button" disabled={index === parts.length - 1 && !semanticLabel} className="max-w-48 truncate text-blue-600 disabled:text-slate-700 dark:disabled:text-slate-200" title={part} onClick={() => navigate(`/${parts.slice(0, index + 1).join('/')}`)}>{part}</button></span>)}{!loading && files && <span className="ml-2 text-xs text-slate-400">{semanticLabel ? '搜索结果' : '本页'} {files.length} 项</span>}</nav><select aria-label="本页文件排序" className={`${quarkInput} ml-auto w-auto max-w-36 py-1.5`} value={sort} onChange={event => setSort(event.target.value as typeof sort)}><option value="updated">本页按修改时间</option><option value="name">本页按文件名</option><option value="size">本页按大小</option></select></div>
        {keyword && <p className="text-xs leading-6 text-slate-500">关键词搜索范围：{dir === '/' ? '当前账号全部目录' : `${dir} 及其子目录`}。</p>}
        {semanticLabel && <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-blue-50 px-3 py-2 text-sm dark:bg-blue-950/30"><span>语义搜索：{semanticLabel}</span><button type="button" className="text-blue-600 underline" onClick={() => void loadFiles()}>返回目录</button></div>}
        {selected.length > 0 && <div className="flex flex-wrap items-center gap-3 text-sm"><span className="text-slate-500">已选择 {selected.length} 项</span><button type="button" className="text-blue-600" onClick={() => setSelected([])}>取消选择</button>{selected.length === 1 && <button type="button" className="text-blue-600 disabled:opacity-40" disabled={!!unsupported('download')} title={unsupported('download')} onClick={() => openTool('download')}>下载</button>}{(['copy', 'move', 'delete'] as const).map(tool => <button key={tool} type="button" className={tool === 'delete' ? 'text-red-600 disabled:opacity-40' : 'text-blue-600 disabled:opacity-40'} title={unsupported(tool)} disabled={!!unsupported(tool)} onClick={() => openTool(tool)}>{{ copy: '复制', move: '移动', delete: '删除' }[tool]}</button>)}</div>}
        {filesError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{filesError}</p>}
        {view === 'grid' && !loading && files && files.length > 0 ? <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-4">{sortedFiles.map(file => <div key={file.fs_id} className={`rounded-lg border p-4 transition-colors ${selected.includes(file.fs_id) ? 'border-blue-200 bg-blue-50/60 dark:border-blue-800 dark:bg-blue-950/30' : 'border-slate-100 hover:border-slate-200 dark:border-slate-800 dark:hover:border-slate-600'}`}><div className="mb-5 flex items-center justify-between"><input type="checkbox" aria-label={`选择 ${file.filename}`} checked={selected.includes(file.fs_id)} disabled={!selected.includes(file.fs_id) && selected.length >= 100} onChange={event => toggle(file, event.target.checked)} /><button type="button" aria-label={`${file.filename}的操作`} aria-haspopup="menu" className="text-slate-400 hover:text-blue-600" onClick={event => showFileMenu(file, event.currentTarget)}><MoreHorizontal className="h-4 w-4" /></button></div><div className="mb-4 flex justify-center"><FileIcon file={file} large /></div><button type="button" className="block w-full truncate text-center text-sm hover:text-blue-600" title={file.filename} onClick={() => file.isdir ? navigate(file.path) : openTool('file-meta', [file])}>{file.filename}</button><p className="mt-2 text-center text-xs tabular-nums text-slate-400">{file.isdir ? '文件夹' : formatBytes(file.size)}</p></div>)}</div> : <div className="overflow-x-auto rounded-md border border-slate-100 dark:border-slate-800"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-slate-500 dark:bg-slate-800/50 dark:text-slate-400"><tr><th className="w-10 px-4 py-3"><input type="checkbox" aria-label="选择本页百度网盘文件" checked={allSelected} disabled={loading || sortedFiles.length === 0} onChange={event => setSelected(event.target.checked ? sortedFiles.slice(0, 100).map(file => file.fs_id) : [])} /></th><th className="px-3 py-3 font-medium">文件名称</th><th className="whitespace-nowrap px-3 py-3 font-medium">大小</th><th className="whitespace-nowrap px-3 py-3 font-medium">修改时间</th><th className="px-4 py-3 font-medium">操作</th></tr></thead><tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {loading ? <tr><td colSpan={5} className="py-14 text-center text-slate-500"><Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-blue-500" />正在读取百度网盘文件…</td></tr> : !files ? <tr><td colSpan={5} className="py-14 text-center text-slate-500">未能读取文件，请点击刷新重试。</td></tr> : files.length === 0 ? <tr><td colSpan={5} className="py-14 text-center text-slate-500">{keyword || semanticLabel ? '未找到匹配文件，请调整搜索内容。' : category === 'all' ? '此目录暂无文件。' : '当前目录没有此类型的文件。'}</td></tr> : sortedFiles.map(file => <tr key={file.fs_id} className={selected.includes(file.fs_id) ? 'bg-blue-50/70 dark:bg-blue-950/30' : 'hover:bg-slate-50/70 dark:hover:bg-slate-800/40'}><td className="px-4 py-3"><input type="checkbox" aria-label={`选择 ${file.filename}`} checked={selected.includes(file.fs_id)} disabled={!selected.includes(file.fs_id) && selected.length >= 100} onChange={event => toggle(file, event.target.checked)} /></td><td className="min-w-52 px-3 py-3.5"><div className="flex items-center gap-3"><FileIcon file={file} /><button type="button" onClick={() => file.isdir ? navigate(file.path) : openTool('file-meta', [file])} title={file.path} className={`break-all text-left hover:underline ${file.isdir ? 'text-blue-600 dark:text-blue-400' : ''}`}>{file.filename}</button></div></td><td className="whitespace-nowrap px-3 py-3 text-slate-500">{file.isdir ? '—' : formatBytes(file.size)}</td><td className="whitespace-nowrap px-3 py-3 text-slate-500">{formatTime(file.updated_at)}</td><td className="px-4 py-3"><button type="button" aria-label={`${file.filename}的操作`} aria-haspopup="menu" className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-blue-600 dark:hover:bg-slate-700" onClick={event => showFileMenu(file, event.currentTarget)}><MoreHorizontal className="h-5 w-5" /></button></td></tr>)}
        </tbody></table></div>}
        {!loading && files && <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-500"><span>本页 {files.length} 项{keyword ? ` · 搜索：${keyword}` : ''} · 每次最多选择 100 项</span>{!semanticLabel && <div className="flex items-center gap-2"><button type="button" className={quarkButton} disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span>第 {page} 页</span><button type="button" className={quarkButton} disabled={!hasMore} onClick={() => setPage(value => value + 1)}>下一页</button></div>}</div>}
        {capabilityError && <p role="alert" className="text-xs text-amber-700 dark:text-amber-400">功能支持信息暂不可用：{capabilityError}</p>}
      </> : <>
        <p className="text-sm leading-7 text-slate-500 dark:text-slate-400">从百度网盘读取当前账号的真实分享记录，包括官网或 App 创建的分享。每页显示最多 100 项，可查看链接、有效期并取消分享。</p>
        {latestShare && !(shares || []).some(share => share.id === latestShare.id) && shareCard(latestShare, true)}
        {sharesError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{sharesError}</p>}
        {sharesLoading ? <div role="status" className="py-12 text-center text-sm text-slate-500"><Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />正在读取百度账号分享…</div> : shares && shares.length === 0 ? <div className="py-12 text-center text-sm text-slate-500">此页暂无分享记录，请选择文件或文件夹创建分享。</div> : <div className="space-y-3">{(shares || []).map(share => shareCard(share))}</div>}
        {shares && !sharesLoading && <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500"><span>本页 {shares.length} 项 · 每页最多 100 项</span><div className="flex items-center gap-2"><button type="button" className={quarkButton} disabled={sharePage <= 1} onClick={() => setSharePage(value => value - 1)}>上一页</button><span>第 {sharePage} 页</span><button type="button" className={quarkButton} disabled={!sharesHasMore} onClick={() => setSharePage(value => value + 1)}>下一页</button></div></div>}
      </>}
    </section>
    {fileMenu && createPortal(<div ref={fileMenuRef} role="menu" aria-label="百度网盘文件操作" className="fixed z-[100] w-44 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-800" style={{ left: fileMenu.x, top: fileMenu.y }}><button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-700" title={unsupported('share', [fileMenu.file])} disabled={!!unsupported('share', [fileMenu.file])} onClick={() => openShare([fileMenu.file])}><Link2 className="h-4 w-4" />创建分享</button>{rowActions.map(([tool, label, Icon]) => <button type="button" role="menuitem" key={tool} title={unsupported(tool, [fileMenu.file])} disabled={!!unsupported(tool, [fileMenu.file])} className={`flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-40 dark:hover:bg-slate-700 ${tool === 'delete' ? 'text-red-600' : ''}`} onClick={() => openTool(tool, [fileMenu.file])}><Icon className="h-4 w-4" />{label}</button>)}</div>, document.body)}
    {toolDraft && <BaiduOperationDialog key={toolDraft.tool} tool={toolDraft.tool} chosen={toolDraft.chosen} dir={dir} onChanged={operationChanged} onSemantic={showSemantic} onClose={() => setToolDraft(null)} />}
    {shareDraft && <QuarkDialog compact title="创建百度网盘分享" busy={shareBusy} error={shareError} onClose={() => setShareDraft(null)}>
      <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submitShare() }}>
        <p className="text-sm leading-7 text-slate-500">已选择 {shareDraft.fsids.length} 项。确认后会在当前百度账号中创建真实分享。</p>
        <label className="block space-y-1 text-sm"><span>分享标题</span><input className={quarkInput} required autoFocus maxLength={100} value={shareDraft.title} onChange={event => setShareDraft({ ...shareDraft, title: event.target.value })} /></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-sm"><span>有效期</span><select className={quarkInput} value={period} onChange={event => setPeriod(Number(event.target.value) as typeof period)}>{periods.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label className="block space-y-1 text-sm"><span>提取码（可选）</span><input className={quarkInput} autoComplete="off" spellCheck={false} maxLength={4} pattern="[A-Za-z0-9]{4}" value={sharePassword} onChange={event => setSharePassword(event.target.value)} placeholder="留空自动生成" /></label>
        </div>
        <p className="text-xs leading-6 text-slate-500">提取码支持 4 位字母或数字，留空由百度生成。创建成功后可复制实际链接和提取码。</p>
        {period === 0 && <p className="text-xs leading-6 text-slate-500">永久分享不会自动到期，后续可从「我的分享」取消。</p>}
        <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={() => setShareDraft(null)}>取消</button><button type="submit" disabled={!shareDraft.title.trim() || !!sharePassword.trim() && !/^[A-Za-z0-9]{4}$/.test(sharePassword.trim())} className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">确认创建分享</button></div>
      </form>
    </QuarkDialog>}
    {cancelDraft && <QuarkDialog compact title="取消百度网盘分享" busy={cancelBusy} error={cancelError} onClose={() => setCancelDraft(null)}>
      <p className="break-all text-sm font-medium">{cancelDraft.title || '未命名分享'}</p>
      <p className="break-all text-xs leading-6 text-slate-500">{cancelDraft.share_url}</p>
      <p className="text-sm leading-7 text-slate-600 dark:text-slate-300">确认后会取消此百度分享，原链接将不能继续使用。已用于发货的链接也会受到影响。</p>
      <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={() => setCancelDraft(null)}>保留分享</button><button type="button" className="inline-flex items-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700" onClick={() => void submitCancelShare()}><Trash2 className="h-4 w-4" />确认取消分享</button></div>
    </QuarkDialog>}
  </div>
}
