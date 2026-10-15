import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronRight, FileText, Folder, Loader2, RefreshCw, Upload, X } from 'lucide-react'
import { getBaiduFiles } from '@/api/baiduDrive'
import { getQuarkFiles } from '@/api/quarkDrive'
import { importCloudAsset, importCloudAssetShare, importCloudAssetUrl, uploadCloudAsset, uploadCloudAssetVersion, type CloudAsset, type CloudAssetAvailability, type CloudProvider } from '@/api/cloudAssets'
import { getApiErrorMessage } from '@/utils/request'
import { formatBytes } from './QuarkFileManager'
import { QuarkDialog, quarkButton, quarkInput } from './QuarkTools'

const providerNames = { quark: '夸克网盘', baidu: '百度网盘' }
interface RemoteFile { id: string; path?: string; name: string; size?: number; folder: boolean }

function ExistingFiles({ provider, selected, maxFiles, onChange }: {
  provider: CloudProvider
  selected: RemoteFile[]
  maxFiles: number
  onChange: (files: RemoteFile[]) => void
}) {
  const root = provider === 'quark' ? { id: '0', name: '全部文件' } : { id: '/', name: '全部文件' }
  const [trail, setTrail] = useState([root])
  const [page, setPage] = useState(1)
  const [files, setFiles] = useState<RemoteFile[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const version = useRef(0)
  const current = trail[trail.length - 1]
  const load = useCallback(async () => {
    const sequence = ++version.current
    setLoading(true)
    setError('')
    setFiles([])
    try {
      if (provider === 'quark') {
        const result = await getQuarkFiles(current.id)
        if (sequence !== version.current) return
        if (!result.success || !result.data) throw new Error(result.message || '读取夸克文件失败')
        const all = result.data.files
        setFiles(all.slice((page - 1) * 50, page * 50).map(file => ({ id: file.fid, name: file.filename || '未命名文件', size: file.size, folder: file.file_type === '0' || file.category === 0 })))
        setHasMore(all.length > page * 50)
      } else {
        const result = await getBaiduFiles({ dir: current.id, page, page_size: 50 })
        if (sequence !== version.current) return
        if (!result.success || !result.data) throw new Error(result.message || '读取百度文件失败')
        setFiles(result.data.files.map(file => ({ id: file.fs_id, path: file.path, name: file.filename, size: file.size, folder: file.isdir })))
        setHasMore(result.data.has_more)
      }
    } catch (reason) { if (sequence === version.current) setError(getApiErrorMessage(reason, '读取网盘文件失败')) }
    finally { if (sequence === version.current) setLoading(false) }
  }, [provider, current.id, page])
  const invalidate = useCallback(() => { version.current++ }, [])
  useEffect(() => { void load(); return invalidate }, [load, invalidate])
  const toggle = (file: RemoteFile, checked: boolean) => onChange(checked ? [...selected, file] : selected.filter(item => item.id !== file.id))

  return <div className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <div className="flex items-center justify-between gap-2"><nav aria-label="选择已有资料" className="flex min-w-0 flex-wrap items-center gap-1 text-sm">{trail.map((part, index) => <span key={part.id} className="inline-flex min-w-0 items-center gap-1">{index > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-400" />}<button type="button" className="max-w-36 truncate text-blue-600" title={part.name} onClick={() => { setTrail(items => items.slice(0, index + 1)); setPage(1) }}>{part.name}</button></span>)}</nav><button type="button" aria-label="刷新已有网盘文件" className={quarkButton} disabled={loading} onClick={() => void load()}><RefreshCw className="h-3.5 w-3.5" /></button></div>
    {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    <div className="max-h-64 divide-y divide-slate-100 overflow-auto dark:divide-slate-700">{loading ? <p role="status" className="flex justify-center gap-2 py-8 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />正在读取文件…</p> : files.length ? files.map(file => <div key={file.id} className="flex items-center gap-3 py-2.5 text-sm">{file.folder ? <Folder className="h-4 w-4 shrink-0 text-amber-500" /> : <input type="checkbox" aria-label={`导入 ${file.name}`} checked={selected.some(item => item.id === file.id)} disabled={selected.length >= maxFiles && !selected.some(item => item.id === file.id)} onChange={event => toggle(file, event.target.checked)} />}<button type="button" className={`min-w-0 flex-1 break-all text-left ${file.folder ? 'text-blue-600' : ''}`} onClick={() => file.folder ? (setTrail(items => [...items, { id: provider === 'baidu' ? file.path! : file.id, name: file.name }]), setPage(1)) : toggle(file, !selected.some(item => item.id === file.id))} disabled={!file.folder && selected.length >= maxFiles && !selected.some(item => item.id === file.id)}>{file.name}</button><span className="shrink-0 text-xs text-slate-500">{file.folder ? '文件夹' : formatBytes(file.size)}</span>{file.folder && <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />}</div>) : !error && <p className="py-8 text-center text-sm text-slate-500">此页暂无文件。</p>}</div>
    <div className="flex items-center justify-between gap-3 text-xs text-slate-500"><span>进入文件夹选择其中的文件，最多 {maxFiles} 项。</span><div className="flex shrink-0 items-center gap-2"><button type="button" disabled={loading || page <= 1} className={quarkButton} onClick={() => setPage(value => value - 1)}>上一页</button><span>{page}</span><button type="button" disabled={loading || !hasMore} className={quarkButton} onClick={() => setPage(value => value + 1)}>下一页</button></div></div>
  </div>
}

export type CloudAssetCreateMode = 'upload' | 'import' | 'url' | 'version'

export function CloudAssetCreateDialog({ mode, availability, asset, onCreated, onClose }: {
  mode: CloudAssetCreateMode
  availability: CloudAssetAvailability
  asset?: CloudAsset
  onCreated: (asset: CloudAsset, message?: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState(asset?.name || '')
  const [uploads, setUploads] = useState<File[]>([])
  const [providers, setProviders] = useState<CloudProvider[]>(availability.providers.filter(item => item.connected).map(item => item.provider))
  const [source, setSource] = useState<CloudProvider>(availability.providers.find(item => item.connected)?.provider || 'quark')
  const [importSource, setImportSource] = useState<'files' | 'share'>('files')
  const [selected, setSelected] = useState<RemoteFile[]>([])
  const [url, setUrl] = useState('')
  const [filename, setFilename] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const maxFiles = typeof availability.limits.max_files === 'number' && availability.limits.max_files > 0 ? availability.limits.max_files : 100
  const maxBytes = typeof availability.limits.max_upload_bytes === 'number' ? availability.limits.max_upload_bytes : undefined
  const totalBytes = uploads.reduce((sum, file) => sum + file.size, 0)
  const connected = availability.providers.find(item => item.provider === source)?.connected
  const localUpload = mode === 'upload' || mode === 'version'
  const titles = { upload: '上传并创建同步资料', import: '导入网盘已有资料', url: '导入公开文件直链', version: '上传资料新版本' }
  const submit = async () => {
    if (busy) return
    setError('')
    if (!name.trim()) { setError('请输入资料名称。'); return }
    if (mode !== 'version' && !providers.length) { setError('请选择至少一个已授权的目标网盘。'); return }
    if (localUpload && !uploads.length) { setError('请选择至少一个本地文件。'); return }
    if (mode === 'version' && !asset) { setError('请重新打开要更新的资料。'); return }
    if (mode === 'import' && (!connected || importSource === 'files' && !selected.length)) { setError('请选择已授权来源网盘和要导入的文件。'); return }
    if ((localUpload ? uploads.length : selected.length) > maxFiles) { setError(`每次最多选择 ${maxFiles} 个文件。`); return }
    if (localUpload && maxBytes && totalBytes > maxBytes) { setError(`本次文件总大小超过 ${formatBytes(maxBytes)} 的项目限制。`); return }
    if (mode === 'url') {
      try { const parsed = new URL(url.trim()); if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error() }
      catch { setError('请输入完整的公开 http 或 https 文件直链，不能包含登录凭证。'); return }
      if (!filename.trim() || /[\\/]/.test(filename) || ['.', '..'].includes(filename.trim())) { setError('请输入有效的保存文件名，包含扩展名。'); return }
    }
    if (mode === 'import' && importSource === 'share') {
      try {
        const parsed = new URL(url.trim())
        if (parsed.protocol !== 'https:' || parsed.hostname !== (source === 'quark' ? 'pan.quark.cn' : 'pan.baidu.com') || parsed.username || parsed.password || !['', '443'].includes(parsed.port)) throw new Error()
      } catch { setError('请输入与来源网盘匹配的完整 https 分享链接。'); return }
    }
    setBusy(true)
    try {
      const result = mode === 'upload' ? await uploadCloudAsset(name.trim(), providers, uploads)
        : mode === 'version' ? await uploadCloudAssetVersion(asset!.id, uploads)
        : mode === 'url' ? await importCloudAssetUrl({ name: name.trim(), url: url.trim(), filename: filename.trim(), providers, confirmed: true })
        : importSource === 'share' ? await importCloudAssetShare({ name: name.trim(), provider: source, providers, url: url.trim(), ...(password.trim() ? { password: password.trim() } : {}), confirmed: true })
        : await importCloudAsset({ name: name.trim(), provider: source, providers, ...(source === 'quark' ? { file_ids: selected.map(file => file.id) } : { paths: selected.map(file => file.path!) }), confirmed: true })
      if (result.data?.id) { onCreated(result.data, result.message); onClose(); return }
      throw new Error(result.message || '资料未创建，请检查请求结果')
    } catch (reason) { setError(`${getApiErrorMessage(reason, '保存资料失败')}。若请求超时，请刷新资源列表核对结果，避免重复提交。`) }
    finally { setBusy(false) }
  }
  const ready = name.trim() && (mode === 'version' || providers.length)
    && (localUpload ? uploads.length : mode === 'import' ? connected && (importSource === 'files' ? selected.length : url.trim()) : url.trim() && filename.trim())

  return <QuarkDialog title={titles[mode]} busy={busy} error={error} onClose={onClose}>
    <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit() }}>
      {mode === 'version' ? <div className="space-y-2 rounded-md bg-blue-50 p-3 text-sm leading-7 dark:bg-blue-950/30"><p className="break-all font-medium">{asset?.name}</p><p>上传后建立新版本，需要继续确认同步和分享。{asset?.published_version != null ? `已发布的版本 ${asset.published_version} 保留，原发货链接不会被此次上传覆盖。` : '当前尚无已发布版本。'}</p></div> : <label className="block space-y-1 text-sm"><span>资料名称</span><input autoFocus required className={quarkInput} maxLength={100} placeholder="例如：短视频运营资料" value={name} onChange={event => setName(event.target.value)} /></label>}
      {mode !== 'version' && <div className="space-y-2"><span className="text-sm">目标网盘</span><div className="grid gap-2 sm:grid-cols-2">{availability.providers.map(item => <label key={item.provider} className={`flex cursor-pointer items-start gap-2 rounded-md border p-3 text-sm ${providers.includes(item.provider) ? 'border-blue-300 bg-blue-50/50 dark:border-blue-700 dark:bg-blue-950/30' : 'border-slate-200 dark:border-slate-700'} ${!item.connected ? 'opacity-60' : ''}`}><input className="mt-1" type="checkbox" checked={providers.includes(item.provider)} disabled={!item.connected} onChange={event => setProviders(values => event.target.checked ? [...values, item.provider] : values.filter(value => value !== item.provider))} /><span><span className="block font-medium">{providerNames[item.provider]}</span><span className="mt-1 block text-xs text-slate-500">{item.connected ? item.verified ? '已连接' : '凭证已保存，待验证' : '尚未授权'}</span>{item.share_notice && <span className="mt-1 block text-xs leading-5 text-amber-700 dark:text-amber-400">{item.share_notice}</span>}</span></label>)}</div></div>}
      {localUpload ? <>
        <label className="flex cursor-pointer flex-col items-center gap-3 rounded-lg border border-dashed border-slate-300 px-4 py-6 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-900"><Upload className="h-7 w-7 text-blue-600" /><span>{mode === 'version' ? '选择新版本的完整文件集' : '选择一个或多个本地文件'}</span><input aria-label="上传同步资料" type="file" multiple className="max-w-full text-xs text-slate-500" onChange={event => { const next = Array.from(event.target.files || []); setUploads(next); if (!name && next.length) setName(next.length === 1 ? next[0].name.slice(0, 100) : `${next[0].name} 等 ${next.length} 个文件`.slice(0, 100)); event.target.value = '' }} /></label>
        {uploads.length > 0 && <div className="space-y-2"><p className="text-xs text-slate-500">已选择 {uploads.length} 个文件 · 共 {formatBytes(totalBytes)}</p><ul className="max-h-36 space-y-2 overflow-auto text-sm">{uploads.map((file, index) => <li key={`${file.name}-${index}`} className="flex items-center gap-2"><FileText className="h-4 w-4 shrink-0 text-slate-400" /><span className="min-w-0 flex-1 break-all">{file.name}</span><span className="shrink-0 text-xs text-slate-500">{formatBytes(file.size)}</span><button type="button" aria-label={`移除 ${file.name}`} className="p-1 text-slate-400 hover:text-red-600" onClick={() => setUploads(values => values.filter((_, item) => item !== index))}><X className="h-4 w-4" /></button></li>)}</ul></div>}
        <p className="text-xs leading-6 text-slate-500">每次最多 {maxFiles} 个文件{maxBytes ? `，总大小不超过 ${formatBytes(maxBytes)}` : ''}。资料与同步进度会保存在服务端，可在资源详情中继续操作。</p>
      </> : mode === 'import' ? <>
        <div className="flex gap-2" aria-label="资料导入来源">{([['files', '我的网盘文件'], ['share', '网盘分享链接']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={importSource === value} className={`${quarkButton} ${importSource === value ? 'border-blue-300 bg-blue-50 text-blue-600 dark:border-blue-700 dark:bg-blue-950/30' : ''}`} onClick={() => { setImportSource(value); setSelected([]); setUrl(''); setPassword('') }}>{label}</button>)}</div>
        <label className="block space-y-1 text-sm"><span>来源网盘</span><select className={quarkInput} value={source} onChange={event => { setSource(event.target.value as CloudProvider); setSelected([]) }}>{availability.providers.map(item => <option key={item.provider} value={item.provider} disabled={!item.connected}>{providerNames[item.provider]}{!item.connected ? '（未授权）' : ''}</option>)}</select></label>
        {importSource === 'files' ? connected ? <ExistingFiles key={source} provider={source} selected={selected} maxFiles={maxFiles} onChange={next => { setSelected(next); if (!name && next.length) setName(next.length === 1 ? next[0].name.slice(0, 100) : `${next[0].name} 等 ${next.length} 个文件`.slice(0, 100)) }} /> : <p className="text-sm text-amber-700 dark:text-amber-400">请先到对应网盘页面完成授权。</p> : <><label className="block space-y-1 text-sm"><span>分享链接</span><input required type="url" className={quarkInput} maxLength={4096} value={url} onChange={event => setUrl(event.target.value)} placeholder={source === 'quark' ? 'https://pan.quark.cn/s/…' : 'https://pan.baidu.com/s/…'} /></label><label className="block space-y-1 text-sm"><span>提取码（可选）</span><input className={quarkInput} autoComplete="off" maxLength={16} pattern="[A-Za-z0-9]{0,16}" value={password} onChange={event => setPassword(event.target.value)} /></label><p className="rounded-md bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">确认后会先在来源网盘转存真实文件，再下载到项目资料副本，后续再单独同步到所选网盘。分享链接无法直接跨平台转存。请选择普通文件，目录请先打包为 ZIP。</p></>}
        {selected.length > 0 && <div className="space-y-2"><p className="text-xs text-slate-500">已选择 {selected.length} 项，可跨目录选择</p><ul className="max-h-28 space-y-1 overflow-auto">{selected.map(file => <li key={file.id} className="flex items-center justify-between gap-2 text-sm"><span className="break-all">{file.name}</span><button type="button" aria-label={`取消导入 ${file.name}`} className="shrink-0 p-1 text-slate-400 hover:text-red-600" onClick={() => setSelected(values => values.filter(item => item.id !== file.id))}><X className="h-4 w-4" /></button></li>)}</ul></div>}
        <p className="text-xs leading-6 text-slate-500">导入后会管理这份资料的项目副本，跨盘同步需要单独确认。可从当前账号网盘目录中选择已有文件。</p>
      </> : <>
        <label className="block space-y-1 text-sm"><span>公开文件直链</span><input type="url" required className={quarkInput} maxLength={4096} placeholder="https://example.com/file.pdf" value={url} onChange={event => setUrl(event.target.value)} /></label>
        <label className="block space-y-1 text-sm"><span>保存文件名（包含扩展名）</span><input required className={quarkInput} maxLength={200} placeholder="资料.pdf" value={filename} onChange={event => setFilename(event.target.value)} /></label>
        <p className="text-xs leading-6 text-slate-500">需要可直接下载的公开文件地址。需要登录的网页、私网地址和网盘分享链接请使用对应导入入口。{maxBytes ? `本次最多 ${formatBytes(maxBytes)}。` : ''}</p>
      </>}
      <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={onClose}>取消</button><button type="submit" disabled={!ready} className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">{mode === 'upload' ? '确认上传资料' : mode === 'version' ? '确认建立新版本' : '确认导入资料'}</button></div>
    </form>
  </QuarkDialog>
}
