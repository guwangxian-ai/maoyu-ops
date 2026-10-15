import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowDownUp, ChevronRight, Copy, Download, ExternalLink, File, FileText, Folder, Grid2X2, History, Image, Link2, List, Loader2, MoreHorizontal, Move, Pencil, PlaySquare, RefreshCw, Search, X } from 'lucide-react'
import { createQuarkShare, getQuarkFiles, getQuarkShares, type QuarkFile, type QuarkShare } from '@/api/quarkDrive'
import { QuarkDialog, QuarkTools, type QuarkTool } from './QuarkTools'
import { QuarkShareTools } from './QuarkShareTools'
import { QuarkTransfers } from './QuarkTransfers'
import { QuarkJobCenter } from './QuarkJobCenter'

const buttonClass = 'inline-flex items-center justify-center gap-1.5 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800'
const inputClass = 'rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100'
const expiryLabels = ['永久有效', '1 天', '7 天', '30 天', '60 天', '100 天', '180 天']
const pageSize = 50

export const formatBytes = (value?: number) => {
  if (value === undefined || value === null || !Number.isFinite(Number(value))) return '—'
  const bytes = Math.max(0, Number(value))
  const unit = Math.min(4, Math.floor(Math.log(bytes || 1) / Math.log(1024)))
  return `${(bytes / 1024 ** unit).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} ${['B', 'KB', 'MB', 'GB', 'TB'][unit]}`
}

const isFolder = (file: QuarkFile) => file.file_type === '0' || file.category === 0
const formatTime = (value?: string | number) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : '请求失败，请稍后重试'
const shareText = (share: QuarkShare) => `${share.share_url}${share.passcode ? `\n提取码：${share.passcode}` : ''}`
function FileIcon({ file, large = false }: { file: QuarkFile; large?: boolean }) {
  const size = large ? 'h-11 w-11' : 'h-6 w-6'
  if (isFolder(file)) return <Folder className={`${size} shrink-0 fill-amber-400 text-amber-400`} strokeWidth={1.3} />
  if (file.category === 3) return <Image className={`${size} shrink-0 text-emerald-500`} />
  if (file.category === 1) return <PlaySquare className={`${size} shrink-0 text-violet-500`} />
  if (file.category === 4 || /\.(pdf|docx?|xlsx?|pptx?)$/i.test(file.filename || '')) return <FileText className={`${size} shrink-0 ${/\.pdf$/i.test(file.filename || '') ? 'text-red-500' : 'text-blue-500'}`} />
  return <File className={`${size} shrink-0 text-slate-400`} />
}

export function QuarkFileManager({ accountControl, statusNotice }: { accountControl: ReactNode; statusNotice?: ReactNode }) {
  const [tab, setTab] = useState<'files' | 'shares' | 'links' | 'transfers' | 'jobs'>('files')
  const [jobRefresh, setJobRefresh] = useState(0)
  const [path, setPath] = useState([{ fid: '0', name: '全部文件' }])
  const parentFid = path[path.length - 1].fid
  const [searchInput, setSearchInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [searchType, setSearchType] = useState('mix')
  const [files, setFiles] = useState<QuarkFile[] | null>(null)
  const [filesLoading, setFilesLoading] = useState(true)
  const [filesError, setFilesError] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [page, setPage] = useState(1)
  const [shares, setShares] = useState<QuarkShare[] | null>(null)
  const [sharesLoading, setSharesLoading] = useState(true)
  const [sharesError, setSharesError] = useState('')
  const [shareDraft, setShareDraft] = useState<{ fids: string[]; title: string } | null>(null)
  const [urlType, setUrlType] = useState<1 | 2>(2)
  const [expiredType, setExpiredType] = useState(1)
  const [creating, setCreating] = useState(false)
  const [shareError, setShareError] = useState('')
  const [notice, setNotice] = useState('')
  const [view, setView] = useState<'list' | 'grid'>('list')
  const [sort, setSort] = useState<'updated' | 'name' | 'size'>('updated')
  const [toolTrigger, setToolTrigger] = useState<{ tool: QuarkTool; files: QuarkFile[]; id: number } | null>(null)
  const [fileMenu, setFileMenu] = useState<{ file: QuarkFile; x: number; y: number } | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [copyError, setCopyError] = useState('')
  const [latestShare, setLatestShare] = useState<QuarkShare | null>(null)
  const fileRequest = useRef(0)
  const shareRequest = useRef(0)
  const invalidateFiles = useCallback(() => { fileRequest.current++ }, [])
  const invalidateShares = useCallback(() => { shareRequest.current++ }, [])

  useEffect(() => {
    if (!fileMenu) return
    const close = (event: MouseEvent) => { if (!menuRef.current?.contains(event.target as Node)) setFileMenu(null) }
    const keydown = (event: KeyboardEvent) => { if (event.key === 'Escape') setFileMenu(null) }
    const scroll = () => setFileMenu(null)
    document.addEventListener('mousedown', close); document.addEventListener('keydown', keydown); window.addEventListener('scroll', scroll, true)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', keydown); window.removeEventListener('scroll', scroll, true) }
  }, [fileMenu])

  const loadFiles = useCallback(async () => {
    const requestId = ++fileRequest.current
    setFilesLoading(true)
    setFilesError('')
    setFiles(null)
    setSelected([])
    setPage(1)
    try {
      const result = await getQuarkFiles(parentFid, keyword, searchType)
      if (requestId !== fileRequest.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取文件列表失败')
      setFiles(result.data.files)
    } catch (reason) {
      if (requestId === fileRequest.current) setFilesError(errorMessage(reason))
    } finally {
      if (requestId === fileRequest.current) setFilesLoading(false)
    }
  }, [parentFid, keyword, searchType])

  const loadShares = useCallback(async () => {
    const requestId = ++shareRequest.current
    setSharesLoading(true)
    setSharesError('')
    try {
      const result = await getQuarkShares()
      if (requestId !== shareRequest.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取分享记录失败')
      setShares(result.data.shares)
    } catch (reason) {
      if (requestId === shareRequest.current) setSharesError(errorMessage(reason))
    } finally {
      if (requestId === shareRequest.current) setSharesLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadFiles()
    return invalidateFiles
  }, [loadFiles, invalidateFiles])

  useEffect(() => {
    void loadShares()
    return invalidateShares
  }, [loadShares, invalidateShares])

  const navigate = (nextPath: typeof path) => {
    setPath(nextPath)
    setSearchInput('')
    setKeyword('')
  }

  const openShare = (fids: string[]) => {
    const chosen = (files || []).filter(file => fids.includes(file.fid))
    const title = chosen.length === 1 ? chosen[0].filename || '文件分享' : `${path[path.length - 1].name} · ${fids.length} 个文件`
    setShareDraft({ fids, title: title.slice(0, 100) })
    setShareError('')
    setUrlType(2)
    setExpiredType(1)
  }

  const submitShare = async () => {
    if (!shareDraft || creating) return
    setCreating(true)
    setShareError('')
    setNotice('')
    try {
      const result = await createQuarkShare({ ...shareDraft, url_type: urlType, expired_type: expiredType })
      if (!result.success || !result.data) throw new Error(result.message || '创建分享失败')
      const created = result.data.share
      setLatestShare(created)
      setShareDraft(null)
      setSelected([])
      setTab('shares')
      setNotice(result.data.message || '分享已创建并保存，可复制链接和提取码用于发货。')
      if (result.data.saved) {
        // Invalidate an earlier history request before inserting the new record.
        shareRequest.current++
        setSharesLoading(false)
        setShares(current => [created, ...(current || []).filter(row => row.id !== created.id)])
        void loadShares()
      }
    } catch (reason) {
      setShareError(errorMessage(reason))
    } finally {
      setCreating(false)
    }
  }

  const copyShare = async (share: QuarkShare) => {
    setCopyError('')
    try {
      await navigator.clipboard.writeText(shareText(share))
      setNotice('分享链接和提取码已复制。')
    } catch {
      setCopyError('浏览器未允许复制，请选中下方完整链接和提取码手动复制。')
    }
  }

  const sortedFiles = [...(files || [])].sort((left, right) => {
    if (isFolder(left) !== isFolder(right)) return isFolder(left) ? -1 : 1
    if (sort === 'name') return (left.filename || '').localeCompare(right.filename || '', 'zh-CN', { numeric: true })
    if (sort === 'size') return (right.size || 0) - (left.size || 0)
    return new Date(right.updated_at || 0).getTime() - new Date(left.updated_at || 0).getTime()
  })
  const visibleFiles = sortedFiles.slice((page - 1) * pageSize, page * pageSize)
  const totalPages = Math.max(1, Math.ceil((files?.length || 0) / pageSize))
  const allPageSelected = visibleFiles.length > 0 && visibleFiles.every(file => selected.includes(file.fid))
  const togglePage = () => setSelected(current => allPageSelected
    ? current.filter(fid => !visibleFiles.some(file => file.fid === fid))
    : [...new Set([...current, ...visibleFiles.map(file => file.fid)])].slice(0, 100))

  return (
    <div className="grid min-h-[36rem] grid-cols-1 md:grid-cols-[10rem_minmax(0,1fr)] lg:grid-cols-[11rem_minmax(0,1fr)]">
      <aside className="flex flex-col border-b border-slate-200 bg-slate-50/40 p-3 md:border-b-0 md:border-r dark:border-slate-700 dark:bg-slate-950/20">
        <nav aria-label="网盘文件导航" className="flex flex-wrap gap-1 md:flex-col">
          {([['mix', '全部文件', Folder], ['album', '图片', Image], ['video', '视频', PlaySquare], ['doc', '文档', FileText]] as const).map(([value, label, Icon]) => <button type="button" key={value} aria-current={tab === 'files' && searchType === value ? 'page' : undefined} onClick={() => { setTab('files'); setSearchType(value); if (value === 'mix') navigate([{ fid: '0', name: '全部文件' }]) }} className={`flex items-center gap-3 rounded-md px-3 py-3 text-left text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 ${tab === 'files' && searchType === value ? 'bg-blue-50 font-medium text-blue-700 dark:bg-blue-950/50 dark:text-blue-300' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'}`}><Icon className="h-4 w-4 shrink-0" />{label}</button>)}
          <div className="my-3 hidden border-t border-slate-200 md:block dark:border-slate-700" />
          {([['shares', '本项目分享', Link2], ['links', '分享转存', ExternalLink], ['transfers', '传输记录', ArrowDownUp], ['jobs', '任务中心', History]] as const).map(([value, label, Icon]) => <button type="button" key={value} aria-current={tab === value ? 'page' : undefined} onClick={() => setTab(value)} className={`flex items-center gap-3 rounded-md px-3 py-3 text-left text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 ${tab === value ? 'bg-blue-50 font-medium text-blue-700 dark:bg-blue-950/50 dark:text-blue-300' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'}`}><Icon className="h-4 w-4 shrink-0" />{label}</button>)}
        </nav>
        <p className="mt-auto hidden px-3 pt-12 text-xs leading-6 text-slate-400 md:block">分类显示当前目录文件。<br />云端删除请在夸克操作。</p>
      </aside>
      <section className="min-w-0 space-y-4 p-4 sm:p-5 lg:p-6">
        {tab === 'files' ? (
            <QuarkTools files={(files || []).filter(file => selected.includes(file.fid))} parentFid={parentFid} folderName={path.map(folder => folder.name).join(' / ')} trigger={toolTrigger} onShare={() => openShare(selected)} onTransfer={() => setTab('links')} accountControl={accountControl} toolbar={<div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
              <form className="relative min-w-32 max-w-64 flex-1" onSubmit={event => { event.preventDefault(); const next = searchInput.trim(); if (next === keyword) void loadFiles(); else setKeyword(next) }}>
                <button type="submit" aria-label="搜索文件" title="搜索文件" disabled={filesLoading} className="absolute inset-y-0 left-0 flex w-9 items-center justify-center text-slate-400 hover:text-blue-600 disabled:opacity-50"><Search className="h-4 w-4" /></button>
                <input aria-label="搜索网盘文件" className={`${inputClass} h-10 w-full min-w-0 pl-9 pr-9`} placeholder={parentFid === '0' ? '搜索网盘文件' : '搜索当前文件夹'} maxLength={50} value={searchInput} onChange={event => setSearchInput(event.target.value)} />
                {(keyword || searchInput) && <button type="button" aria-label="清除搜索" title="清除搜索" className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-slate-400 hover:text-blue-600" onClick={() => { setKeyword(''); setSearchInput('') }}><X className="h-4 w-4" /></button>}
              </form>
              <button type="button" className={buttonClass} aria-label="刷新文件" title="刷新文件" disabled={filesLoading} onClick={() => void loadFiles()}><RefreshCw className={`h-4 w-4 ${filesLoading ? 'animate-spin' : ''}`} /></button>
              <div className="flex rounded-md border border-slate-200 p-0.5 dark:border-slate-700">{([['list', List, '列表视图'], ['grid', Grid2X2, '网格视图']] as const).map(([value, Icon, label]) => <button type="button" key={value} aria-label={label} aria-pressed={view === value} title={label} onClick={() => setView(value)} className={`rounded p-2 ${view === value ? 'bg-blue-50 text-blue-600 dark:bg-blue-950/40' : 'text-slate-400 hover:text-blue-600'}`}><Icon className="h-4 w-4" /></button>)}</div>
            </div>} onJob={() => { setJobRefresh(value => value + 1); setNotice('任务已提交，可在「任务中心」查看进度和结果。') }} />
        ) : <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 border-b border-slate-100 pb-3 dark:border-slate-800">
          <div className="flex min-h-10 min-w-0 flex-wrap items-center gap-3">
            <h2 className="font-semibold">{{ shares: '本项目分享', links: '分享查看与转存', transfers: '传输记录', jobs: '任务中心' }[tab]}</h2>
            {tab === 'shares' && <button type="button" className={buttonClass} disabled={sharesLoading} onClick={() => void loadShares()}><RefreshCw className="h-4 w-4" />刷新分享</button>}
          </div>
          <div className="justify-self-end">{accountControl}</div>
        </div>}
        {statusNotice}
        {notice && <p role="status" className="text-sm text-green-700 dark:text-green-400">{notice}</p>}
        {copyError && <p role="alert" className="text-sm text-amber-700 dark:text-amber-400">{copyError}</p>}

        {tab === 'files' ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <nav aria-label="网盘目录路径" className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
              {path.map((folder, index) => <span key={`${folder.fid}-${index}`} className="inline-flex items-center gap-1">
                {index > 0 && <ChevronRight className="h-4 w-4 text-slate-400" />}
                <button type="button" onClick={() => navigate(path.slice(0, index + 1))} disabled={index === path.length - 1} className="max-w-60 truncate text-blue-600 disabled:text-slate-700 dark:disabled:text-slate-200" title={folder.name}>{folder.name}</button>
              </span>)}
              {!filesLoading && files && <span className="ml-2 whitespace-nowrap text-xs text-slate-400">共 {files.length} 项</span>}
              </nav>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <select aria-label="搜索文件类型" className={`${inputClass} max-w-28 py-1.5`} value={searchType} onChange={event => setSearchType(event.target.value)}>{[['mix', '全部类型'], ['video', '视频'], ['album', '图片'], ['doc', '文档'], ['audio', '音频'], ['dir', '文件夹'], ['package', '压缩包'], ['app', '应用'], ['other', '其他']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                <select aria-label="文件排序" className={`${inputClass} max-w-32 py-1.5`} value={sort} onChange={event => { setSort(event.target.value as typeof sort); setPage(1) }}><option value="updated">按修改时间</option><option value="name">按文件名</option><option value="size">按大小</option></select>
              </div>
            </div>

            {filesError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{filesError}</p>}
            {view === 'grid' && !filesLoading && files && files.length > 0 ? <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-4">{visibleFiles.map(file => <div key={file.fid} className={`group relative rounded-lg border p-4 transition-colors ${selected.includes(file.fid) ? 'border-blue-200 bg-blue-50/60 dark:border-blue-800 dark:bg-blue-950/30' : 'border-slate-100 hover:border-slate-200 dark:border-slate-800 dark:hover:border-slate-600'}`}><div className="mb-5 flex items-center justify-between"><input type="checkbox" aria-label={`选择 ${file.filename || '文件'}`} checked={selected.includes(file.fid)} disabled={!selected.includes(file.fid) && selected.length >= 100} onChange={event => setSelected(current => event.target.checked ? [...current, file.fid] : current.filter(fid => fid !== file.fid))} /><button type="button" aria-label={`${file.filename}的操作`} onClick={event => { const rect = event.currentTarget.getBoundingClientRect(); setFileMenu({ file, x: Math.max(8, rect.right - 168), y: Math.min(rect.bottom + 4, window.innerHeight - 260) }) }} className="text-slate-400 hover:text-blue-600"><MoreHorizontal className="h-4 w-4" /></button></div><div className="mb-4 flex justify-center"><FileIcon file={file} large /></div>{isFolder(file) ? <button type="button" className="block w-full truncate text-center text-sm hover:text-blue-600" title={file.filename} onClick={() => navigate([...path, { fid: file.fid, name: file.filename || '文件夹' }])}>{file.filename}</button> : <p className="truncate text-center text-sm" title={file.filename}>{file.filename}</p>}<p className="mt-2 text-center text-xs tabular-nums text-slate-400">{isFolder(file) ? '文件夹' : formatBytes(file.size)}</p></div>)}</div> : <div className="overflow-x-auto rounded-md border border-slate-100 dark:border-slate-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800/50 dark:text-slate-400"><tr>
                  <th className="w-10 px-4 py-3"><input aria-label="选择本页文件" type="checkbox" checked={allPageSelected} disabled={filesLoading || visibleFiles.length === 0} onChange={togglePage} /></th>
                  <th className="px-3 py-3 font-medium">文件名称</th><th className="whitespace-nowrap px-3 py-3 font-medium">大小 / 文件数量</th><th className="whitespace-nowrap px-3 py-3 font-medium">修改时间</th><th className="px-4 py-3 font-medium">操作</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 text-slate-700 dark:divide-slate-800 dark:text-slate-200">
                  {filesLoading ? <tr><td colSpan={5} className="py-14 text-center"><Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-blue-500" />正在读取夸克文件…</td></tr>
                    : files && files.length === 0 ? <tr><td colSpan={5} className="py-14 text-center text-slate-500">{keyword ? '未找到匹配文件，请调整关键词。' : '此目录暂无文件。若授权时限制了范围，请在夸克 App 中检查文件授权范围。'}</td></tr>
                    : visibleFiles.map(file => <tr key={file.fid} className={`transition-colors ${selected.includes(file.fid) ? 'bg-blue-50/70 dark:bg-blue-950/30' : 'hover:bg-slate-50/70 dark:hover:bg-slate-800/40'}`}>
                      <td className="px-4 py-3"><input type="checkbox" aria-label={`选择 ${file.filename || '文件'}`} checked={selected.includes(file.fid)} disabled={!selected.includes(file.fid) && selected.length >= 100} onChange={event => setSelected(current => event.target.checked ? [...current, file.fid] : current.filter(fid => fid !== file.fid))} /></td>
                      <td className="min-w-52 px-3 py-3.5"><div className="flex items-center gap-3"><FileIcon file={file} />
                        {isFolder(file) ? <button type="button" onClick={() => navigate([...path, { fid: file.fid, name: file.filename || '文件夹' }])} className="break-all text-left text-blue-600 hover:underline dark:text-blue-400">{file.filename || '未命名文件夹'}</button> : <span className="break-all">{file.filename || '未命名文件'}</span>}
                      </div></td>
                      <td className="whitespace-nowrap px-3 py-3 text-slate-500">{isFolder(file) ? file.includeItems !== undefined ? `${file.includeItems} 个文件` : '—' : formatBytes(file.size)}</td>
                      <td className="whitespace-nowrap px-3 py-3 text-slate-500">{formatTime(file.updated_at)}</td>
                      <td className="px-4 py-3"><button type="button" aria-label={`${file.filename}的操作`} aria-haspopup="menu" onClick={event => { const rect = event.currentTarget.getBoundingClientRect(); setFileMenu({ file, x: Math.max(8, rect.right - 168), y: Math.min(rect.bottom + 4, window.innerHeight - 260) }) }} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-blue-600 dark:hover:bg-slate-700"><MoreHorizontal className="h-5 w-5" /></button></td>
                    </tr>)}
                </tbody>
              </table>
            </div>}
            {!filesLoading && files && <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-500">
              <span>共 {files.length} 项{keyword ? ` · 搜索：${keyword}` : ''} · 每次最多选择 100 项</span>
              {totalPages > 1 && <div className="flex items-center gap-2"><button className={buttonClass} disabled={page <= 1} onClick={() => setPage(current => current - 1)}>上一页</button><span>{page} / {totalPages}</span><button className={buttonClass} disabled={page >= totalPages} onClick={() => setPage(current => current + 1)}>下一页</button></div>}
            </div>}
          </>
        ) : tab === 'shares' ? (
          <>
            <p className="text-sm text-slate-500 dark:text-slate-400">这里保存本系统创建的分享。夸克官方 CLI 暂不提供账号全部历史分享的查询；在夸克 App 或网页创建的分享请到夸克查看。</p>
            {latestShare && <div className="space-y-2 rounded-lg border border-blue-200 bg-blue-50/50 p-4 dark:border-blue-900 dark:bg-blue-950/20"><h3 className="text-sm font-medium text-slate-800 dark:text-slate-200">刚创建的分享：{latestShare.title}</h3><a href={latestShare.share_url} target="_blank" rel="noopener noreferrer" className="block break-all text-sm text-blue-600 underline dark:text-blue-400">{latestShare.share_url}</a>{latestShare.passcode && <p className="text-sm">提取码：{latestShare.passcode}</p>}<button type="button" className={buttonClass} onClick={() => void copyShare(latestShare)}><Copy className="h-4 w-4" />复制发货链接</button></div>}
            {sharesError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{sharesError}</p>}
            {sharesLoading ? <div className="py-12 text-center text-sm text-slate-500"><Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />正在读取分享记录…</div>
              : shares && shares.length === 0 ? <div className="py-12 text-center text-sm text-slate-500">还没有本系统创建的分享，请在「全部文件」选择文件或文件夹创建。</div>
              : <div className="space-y-3">{(shares || []).map(share => <div key={share.id} className="space-y-2 rounded-lg border border-slate-200 p-4 dark:border-slate-700">
                <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="break-all text-sm font-medium text-slate-800 dark:text-slate-100">{share.title}</h3><p className="mt-1 text-xs text-slate-500">{formatTime(share.created_at)} · {share.url_type === 2 ? '有提取码' : '公开链接'} · {expiryLabels[share.expired_type - 1] || '—'}</p></div>
                  <button type="button" className={buttonClass} onClick={() => void copyShare(share)}><Copy className="h-4 w-4" />复制发货链接</button>
                </div><a href={share.share_url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 break-all text-sm text-blue-600 underline dark:text-blue-400">{share.share_url}<ExternalLink className="h-3.5 w-3.5 shrink-0" /></a>{share.passcode && <p className="text-sm text-slate-600 dark:text-slate-300">提取码：{share.passcode}</p>}
              </div>)}</div>}
          </>
        ) : tab === 'links' ? <QuarkShareTools onJob={() => setJobRefresh(value => value + 1)} /> : tab === 'transfers' ? <QuarkTransfers onJob={() => setJobRefresh(value => value + 1)} /> : null}
        {tab === 'files' && <p className="text-xs text-slate-400">文件操作调用夸克真实接口。需要删除云端文件时，请在夸克网盘操作。</p>}
        <div className={tab === 'jobs' ? '' : 'hidden'}><QuarkJobCenter refreshKey={jobRefresh} onChanged={() => { void loadFiles(); void loadShares() }} /></div>
      </section>
      {fileMenu && createPortal(<div ref={menuRef} role="menu" aria-label="文件操作" className="fixed z-[100] w-44 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-800" style={{ left: fileMenu.x, top: fileMenu.y }}><button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-700" onClick={() => { openShare([fileMenu.file.fid]); setFileMenu(null) }}><Link2 className="h-4 w-4" />创建分享</button>{([['download', '下载', Download], ['move', '移动', Move], ['rename', '重命名', Pencil], ['summary', '文件总结', FileText]] as const).map(([tool, label, Icon]) => <button type="button" role="menuitem" key={tool} disabled={tool === 'download' && isFolder(fileMenu.file)} className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-40 dark:hover:bg-slate-700" onClick={() => { setToolTrigger({ tool, files: [fileMenu.file], id: Date.now() }); setFileMenu(null) }}><Icon className="h-4 w-4" />{label}</button>)}</div>, document.body)}

      {shareDraft && <QuarkDialog compact title="创建夸克分享" busy={creating} error={shareError} onClose={() => setShareDraft(null)}>
        <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submitShare() }}>
          <p className="text-sm text-slate-500">已选择 {shareDraft.fids.length} 项。点击创建后，会在夸克生成真实分享链接。</p>
          <label className="block space-y-1 text-sm text-slate-700 dark:text-slate-200"><span>分享标题</span><input autoFocus required disabled={creating} maxLength={100} value={shareDraft.title} onChange={event => setShareDraft({ ...shareDraft, title: event.target.value })} className={`${inputClass} w-full`} /></label>
          <div className="grid grid-cols-2 gap-3"><label className="space-y-1 text-sm text-slate-700 dark:text-slate-200"><span>链接类型</span><select disabled={creating} className={`${inputClass} w-full`} value={urlType} onChange={event => setUrlType(Number(event.target.value) as 1 | 2)}><option value={2}>有提取码</option><option value={1}>公开链接</option></select></label>
            <label className="space-y-1 text-sm text-slate-700 dark:text-slate-200"><span>有效期</span><select disabled={creating} className={`${inputClass} w-full`} value={expiredType} onChange={event => setExpiredType(Number(event.target.value))}>{expiryLabels.map((label, index) => <option key={label} value={index + 1}>{label}</option>)}</select></label></div>
          <div className="flex justify-end gap-2"><button type="button" className={buttonClass} disabled={creating} onClick={() => setShareDraft(null)}>取消</button><button type="submit" disabled={creating || !shareDraft.title.trim()} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">{creating && <Loader2 className="h-4 w-4 animate-spin" />}{creating ? '正在创建…' : '创建分享'}</button></div>
        </form>
      </QuarkDialog>}
    </div>
  )
}
