import { useEffect, useState } from 'react'
import { Download, ExternalLink, FileText, Folder, Loader2, Upload } from 'lucide-react'
import { downloadBaiduFile, runBaiduOperation, uploadBaiduFiles, type BaiduFile, type BaiduOperation, type BaiduOperationPayload, type BaiduOperationResult, type BaiduUploadResult } from '@/api/baiduDrive'
import { getApiErrorMessage } from '@/utils/request'
import { formatBytes } from './QuarkFileManager'
import { QuarkDialog, quarkButton, quarkInput } from './QuarkTools'
import { BaiduFolderPicker } from './BaiduFolderPicker'

export type BaiduTool = BaiduOperation | 'upload' | 'download'
const labels: Record<BaiduTool, string> = {
  'create-folder': '新建百度网盘文件夹', copy: '复制文件', move: '移动文件', rename: '重命名文件', delete: '删除文件',
  'file-meta': '文件详情', 'semantic-search': '语义搜索', 'upload-url': '从链接上传', 'upload-text': '上传文本', upload: '上传文件', download: '下载文件', 'share-transfer': '转存百度分享',
}
const rootPath = '/'
const statusLabels: Record<string, string> = { success: '已完成', ok: '已完成', partial: '部分完成', failed: '未完成', submitted: '已提交，等待完成' }
const operationMessage = (value: BaiduOperationResult, fallback?: string) => {
  const message = value.message || fallback || '网盘接口已返回结果，请核对云端文件。'
  if (value.status === 'partial') return `部分操作已完成。${message}`
  if (value.status === 'failed') return `操作未完成。${message}`
  if (value.status === 'submitted') return `任务已提交，仍需等待并核对完成状态。${message}`
  return message
}
const displayPath = (path: string) => path || '/'
const officialLink = (value: unknown) => {
  if (typeof value !== 'string') return undefined
  try {
    const url = new URL(value)
    if (url.protocol === 'https:' && (url.hostname === 'baidu.com' || url.hostname.endsWith('.baidu.com')) && !url.username && !url.password && ['', '443'].includes(url.port)) return value
  } catch { /* Opaque or invalid values cannot be used as browser links. */ }
  return undefined
}

export function BaiduOperationDialog({ tool, chosen, dir, onChanged, onSemantic, onClose }: {
  tool: BaiduTool
  chosen: BaiduFile[]
  dir: string
  onChanged: (message: string) => void
  onSemantic: (files: BaiduFile[], query: string) => void
  onClose: () => void
}) {
  const [busy, setBusy] = useState(tool === 'file-meta')
  const [error, setError] = useState('')
  const [name, setName] = useState(tool === 'rename' ? chosen[0]?.filename || '' : '')
  const [targetDir, setTargetDir] = useState(dir === '/' ? rootPath : dir)
  const [picking, setPicking] = useState(false)
  const [query, setQuery] = useState('')
  const [url, setUrl] = useState('')
  const [password, setPassword] = useState('')
  const [content, setContent] = useState('')
  const [uploads, setUploads] = useState<File[]>([])
  const [result, setResult] = useState<BaiduOperationResult | null>(null)
  const [uploadResult, setUploadResult] = useState<BaiduUploadResult | null>(null)

  useEffect(() => {
    if (tool !== 'file-meta') return
    let active = true
    void runBaiduOperation('file-meta', { dir: dir === '/' ? rootPath : dir, paths: chosen.map(file => file.path), fsids: chosen.map(file => file.fs_id) }).then(response => {
      if (!active) return
      if (!response.success || !response.data) throw new Error(response.message || '读取文件详情失败')
      setResult(response.data)
    }).catch(reason => { if (active) setError(getApiErrorMessage(reason, '读取文件详情失败')) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [tool, chosen, dir])

  const submit = async () => {
    if (busy || result || tool === 'file-meta') return
    setError('')
    if (['create-folder', 'rename', 'upload-text'].includes(tool) && (!name.trim() || /[\\/]/.test(name) || name.trim() === '.' || name.trim() === '..')) {
      setError('名称不能为空，也不能包含斜杠或反斜杠。')
      return
    }
    if (['copy', 'move', 'share-transfer'].includes(tool) && !targetDir.trim().startsWith('/')) {
      setError('目标目录需要以 / 开头，或通过选择目录按钮选取。')
      return
    }
    if (tool === 'semantic-search' && !query.trim()) { setError('请输入想寻找的文件内容。'); return }
    if (tool === 'upload-url') {
      try { const parsed = new URL(url.trim()); if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error() }
      catch { setError('请输入完整的 http 或 https 文件地址。'); return }
    }
    if (tool === 'share-transfer') {
      try {
        const parsed = new URL(url.trim())
        if (parsed.protocol !== 'https:' || parsed.hostname !== 'pan.baidu.com' || parsed.username || parsed.password || !['', '443'].includes(parsed.port)) throw new Error()
      } catch { setError('请输入完整的百度网盘 https 分享链接。'); return }
      if (password.trim() && !/^[A-Za-z0-9]{4}$/.test(password.trim())) { setError('提取码需要 4 位字母或数字。'); return }
    }
    if (tool === 'download' && (chosen.length !== 1 || chosen[0].isdir)) { setError('每次请选择一个普通文件下载。目录下载暂未接入，请先打包为 ZIP 文件。'); return }
    if (tool === 'upload-text' && !content.trim()) { setError('请输入要上传的文本内容。'); return }
    if (tool === 'upload' && uploads.length === 0) { setError('请选择至少一个文件。'); return }
    setBusy(true)
    try {
      if (tool === 'download') {
        const downloaded = await downloadBaiduFile(chosen[0].path)
        const filename = downloaded.filename || `${chosen[0].filename}${chosen[0].isdir ? '.zip' : ''}`
        const objectUrl = URL.createObjectURL(downloaded.blob)
        const anchor = document.createElement('a')
        anchor.href = objectUrl
        anchor.download = filename
        document.body.append(anchor)
        anchor.click()
        anchor.remove()
        setTimeout(() => URL.revokeObjectURL(objectUrl), 60000)
        setResult({ status: 'success', message: '文件已获取，已交给浏览器下载。', data: { filename, size: downloaded.blob.size } })
        return
      }
      if (tool === 'upload') {
        const response = await uploadBaiduFiles(uploads, dir === '/' ? rootPath : dir)
        if (!response.data || !Array.isArray(response.data.results)) throw new Error(response.message || '上传结果不完整，请刷新网盘核验')
        setUploadResult(response.data)
        // Clear the batch after a confirmed result so retry cannot resend successful files.
        setUploads([])
        if (response.data.results.some(file => file.status === 'success' || file.status === 'ok')) onChanged(response.data.message || '上传接口已返回结果，请查看文件列表。')
        if (response.data.status === 'failed') setError(response.data.message || '此次上传未成功，请查看每个文件的错误。')
        return
      }
      const payload: BaiduOperationPayload = {
        dir: dir === '/' ? rootPath : dir,
        ...(tool === 'semantic-search' ? { query: query.trim() } : { confirmed: true }),
        ...(['copy', 'move', 'rename', 'delete'].includes(tool) ? { paths: chosen.map(file => file.path) } : {}),
        ...(['create-folder', 'rename'].includes(tool) ? { name: name.trim() } : {}),
        ...(['copy', 'move', 'share-transfer'].includes(tool) ? { target_dir: targetDir.trim() } : {}),
        ...(tool === 'upload-url' ? { url: url.trim() } : {}),
        ...(tool === 'upload-text' ? { filename: name.trim(), content } : {}),
        ...(tool === 'share-transfer' ? { url: url.trim(), ...(password.trim() ? { password: password.trim() } : {}) } : {}),
      }
      const response = await runBaiduOperation(tool, payload)
      if (!response.data) throw new Error(response.message || '操作未完成')
      if (tool === 'semantic-search') {
        if (!response.success) throw new Error(response.message || '搜索未完成')
        if (!Array.isArray(response.data.files)) throw new Error('百度未返回可展示的搜索结果，请调整描述后重试')
        onSemantic(response.data.files, query.trim())
        onClose()
      } else {
        setResult(response.data)
        const message = operationMessage(response.data, response.message)
        if (!response.success || response.data.status === 'failed') setError(message)
        else onChanged(message)
      }
    } catch (reason) {
      const message = getApiErrorMessage(reason, '请求未完成，请检查云端状态后再重试')
      setError(`${message}${tool === 'semantic-search' ? '' : '。若请求超时，请先刷新目录确认云端结果后再重试。'}`)
    } finally { setBusy(false) }
  }

  const doneUploading = uploadResult && uploads.length === 0
  const canSubmit = !busy && !result && !doneUploading && (tool !== 'download' || !chosen[0]?.isdir)
  const metadata = result?.files?.[0] || chosen[0]
  const uploaded = uploadResult?.results.filter(file => file.status === 'success' || file.status === 'ok').length || 0
  const uploadFailed = uploadResult?.results.filter(file => file.status === 'failed').length || 0
  const resultData = result?.data && typeof result.data === 'object' && !Array.isArray(result.data) ? result.data as Record<string, unknown> : {}
  const returnUrl = officialLink(metadata?.return_url || result?.return_url || resultData.return_url)

  return <QuarkDialog title={labels[tool]} busy={busy} error={error} onClose={onClose} compact={tool === 'create-folder' || tool === 'rename' || tool === 'delete'}>
    {tool === 'file-meta' ? <>
      {metadata && <dl className="space-y-3 text-sm">{[['文件名', metadata.filename], ['路径', displayPath(metadata.path)], ['类型', metadata.isdir ? '文件夹' : '文件'], ['大小', metadata.isdir ? '—' : formatBytes(metadata.size)], ['修改时间', metadata.updated_at ? new Date(metadata.updated_at).toLocaleString('zh-CN', { hour12: false }) : '—'], ['MD5', metadata.md5 || '—']].map(([label, value]) => <div key={label} className="grid grid-cols-[6rem_minmax(0,1fr)] gap-3"><dt className="text-slate-500">{label}</dt><dd className="break-all">{value}</dd></div>)}</dl>}
      {returnUrl && <a href={returnUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="inline-flex items-center gap-1 text-sm text-blue-600 underline">点击查看<ExternalLink className="h-3.5 w-3.5" /></a>}
      <p className="text-xs leading-6 text-slate-500">{metadata?.isdir ? '目录下载暂未接入，请先将资料打包为 ZIP 文件后下载。' : '可从文件操作菜单选择下载。服务端准备完成后浏览器会开始下载，大文件需要等待。'}</p>
      {metadata?.abstract && <p className="whitespace-pre-wrap text-sm leading-7">{metadata.abstract}</p>}
      {metadata?.content && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-900">{metadata.content}</pre>}
      {result && <details className="text-sm"><summary className="cursor-pointer text-slate-500">接口返回详情</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-50 p-3 text-xs dark:bg-slate-900">{JSON.stringify(result.files || result.data, null, 2)}</pre></details>}
      <div className="flex justify-end"><button type="button" className={quarkButton} onClick={onClose}>关闭</button></div>
    </> : <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit() }}>
      {['copy', 'move', 'rename', 'delete', 'download'].includes(tool) ? <>
        <p className="text-sm text-slate-500">已选择 {chosen.length} 项：</p><ul className="max-h-40 space-y-2 overflow-auto rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-900">{chosen.map(file => <li key={file.fs_id} className="flex items-center gap-2">{file.isdir ? <Folder className="h-4 w-4 shrink-0 text-amber-500" /> : <FileText className="h-4 w-4 shrink-0 text-slate-400" />}<span className="break-all">{displayPath(file.path)}</span></li>)}</ul>
      </> : <p className="break-all text-sm text-slate-500">{tool === 'semantic-search' ? '用自然语言描述文件内容，搜索当前账号目录。' : `当前资料目录：${displayPath(dir)}`}</p>}
      {tool === 'download' && <p className="rounded-md bg-blue-50 p-3 text-sm leading-7 dark:bg-blue-950/30">{chosen[0]?.isdir ? '目录下载暂未接入，请先打包为 ZIP 文件。' : '文件会先通过网盘接口下载到服务端。准备完成后浏览器才会开始下载，请保持页面打开；大文件可能需要较长时间。'}</p>}
      {['create-folder', 'rename', 'upload-text'].includes(tool) && <label className="block space-y-1 text-sm"><span>{tool === 'upload-text' ? '文件名（包含扩展名）' : tool === 'rename' ? '新名称' : '文件夹名称'}</span><input className={quarkInput} autoFocus maxLength={255} required value={name} onChange={event => setName(event.target.value)} placeholder={tool === 'upload-text' ? '发货说明.txt' : ''} /></label>}
      {['copy', 'move', 'share-transfer'].includes(tool) && <div className="space-y-3"><label className="block space-y-1 text-sm"><span>目标目录</span><input required className={quarkInput} value={targetDir} onChange={event => setTargetDir(event.target.value)} placeholder="/目标目录" /><span className="block text-xs leading-6 text-slate-500">/ 表示百度网盘根目录。</span></label><button type="button" className={quarkButton} onClick={() => setPicking(value => !value)}><Folder className="h-4 w-4" />{picking ? '收起目录选择' : '选择目标目录'}</button>{picking && <BaiduFolderPicker initialDir={dir} onSelect={value => { setTargetDir(value); setPicking(false) }} />}</div>}
      {tool === 'delete' && <p className="rounded-md bg-red-50 px-3 py-2 text-sm leading-7 text-red-700 dark:bg-red-950/30 dark:text-red-300">点击「确认删除」会删除上方文件或文件夹，文件夹内的内容也会一起删除。请先核对对象和数量，再确认删除。</p>}
      {tool === 'semantic-search' && <label className="block space-y-1 text-sm"><span>搜索描述</span><textarea className={`${quarkInput} min-h-24`} required autoFocus value={query} onChange={event => setQuery(event.target.value)} maxLength={1000} placeholder="例如：介绍短视频运营方法的文档" /></label>}
      {tool === 'upload-url' && <label className="block space-y-1 text-sm"><span>文件下载地址</span><input type="url" className={quarkInput} required value={url} onChange={event => setUrl(event.target.value)} placeholder="https://example.com/file.pdf" /><span className="block text-xs leading-6 text-slate-500">需要可直接下载的文件地址，网页地址或需要登录的链接可能无法上传。</span></label>}
      {tool === 'share-transfer' && <div className="space-y-3"><label className="block space-y-1 text-sm"><span>百度网盘分享链接</span><input type="url" required className={quarkInput} value={url} onChange={event => setUrl(event.target.value)} placeholder="https://pan.baidu.com/s/…" maxLength={4096} /></label><label className="block space-y-1 text-sm"><span>提取码（可选）</span><input className={quarkInput} value={password} onChange={event => setPassword(event.target.value)} maxLength={4} autoComplete="off" placeholder="如分享需要提取码，请填写" /></label><p className="text-xs leading-6 text-slate-500">确认后会将分享内容转存到上方目标目录。若百度返回任务已提交，请等待并核对云端结果，避免重复转存。</p></div>}
      {tool === 'upload-text' && <label className="block space-y-1 text-sm"><span>文本内容</span><textarea className={`${quarkInput} min-h-40 resize-y`} required value={content} onChange={event => setContent(event.target.value)} maxLength={20000} placeholder="输入要保存到网盘的文本" /><span className="block text-xs text-slate-500">{content.length.toLocaleString()} / 20,000 字符</span></label>}
      {tool === 'upload' && <div className="space-y-3"><label className="flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-slate-300 px-4 py-7 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-900"><Upload className="h-7 w-7 text-blue-500" /><span>选择一个或多个文件</span><input aria-label="选择上传到百度网盘的文件" type="file" multiple className="max-w-full text-xs text-slate-500" onChange={event => { setUploads(Array.from(event.target.files || [])); setUploadResult(null); setError(''); event.target.value = '' }} /></label><p className="text-xs leading-6 text-slate-500">支持普通多文件上传，上传到上方目录。上传过程中请保持页面打开。</p>{uploads.length > 0 && <ul className="max-h-36 space-y-2 overflow-y-auto text-sm">{uploads.map((file, index) => <li key={`${file.name}-${index}`} className="flex justify-between gap-3"><span className="break-all">{file.name}</span><span className="shrink-0 text-slate-500">{formatBytes(file.size)}</span></li>)}</ul>}</div>}
      {uploadResult && <div role="status" className="space-y-3 rounded-md border border-slate-200 p-3 text-sm dark:border-slate-700">
        <p className={uploadResult.status === 'success' ? 'text-green-700 dark:text-green-400' : uploadResult.status === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-amber-700 dark:text-amber-400'}>{statusLabels[uploadResult.status]}：{uploadResult.message || '网盘上传接口已返回结果。'}</p>
        <p>完成 {uploaded} 项，未完成 {uploadFailed} 项。</p>
        <ul className="max-h-64 space-y-3 overflow-auto">{uploadResult.results.map((file, index) => <li key={index} className="space-y-1 break-all"><p className={file.status === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-slate-700 dark:text-slate-200'}>{file.filename} · {statusLabels[file.status] || file.status}</p>{file.message && <p className="text-xs leading-6">{file.message}</p>}{file.path && <p className="text-xs text-slate-500">{displayPath(file.path)}</p>}{officialLink(file.return_url) && <a href={officialLink(file.return_url)} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="inline-flex items-center gap-1 text-xs text-blue-600 underline">点击查看<ExternalLink className="h-3 w-3" /></a>}</li>)}</ul>
        {uploadResult.status !== 'success' && <p className="text-xs leading-6 text-slate-500">请检查云端结果，重新选择需要上传的失败文件。已完成文件不会自动重试。</p>}
      </div>}
      {result && <div role="status" className="space-y-3 rounded-md border border-slate-200 p-3 text-sm dark:border-slate-700">
        <p className={result.status === 'failed' ? 'text-red-600 dark:text-red-400' : result.status === 'partial' || result.status === 'submitted' ? 'text-amber-700 dark:text-amber-400' : undefined}>{operationMessage(result)}</p>
        {returnUrl && <a href={returnUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="inline-flex items-center gap-1 text-blue-600 underline">点击查看<ExternalLink className="h-3.5 w-3.5" /></a>}
        {result.results && <ul className="max-h-64 space-y-3 overflow-auto">{result.results.map((item, index) => <li key={index} className="space-y-1 break-all"><p className={item.status === 'failed' ? 'text-red-600 dark:text-red-400' : undefined}>{item.path ? displayPath(item.path) : `第 ${index + 1} 项`} · {statusLabels[item.status] || item.status}</p>{item.destination && <p className="text-xs text-slate-500">目标：{displayPath(item.destination)}</p>}{item.message && <p className="text-xs leading-6">{item.message}</p>}{officialLink(item.return_url) && <a href={officialLink(item.return_url)} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="inline-flex items-center gap-1 text-xs text-blue-600 underline">点击查看<ExternalLink className="h-3 w-3" /></a>}</li>)}</ul>}
        {result.count != null && <p>返回 {result.count} 项{result.status === 'submitted' ? '，等待任务完成' : ''}。</p>}
        {result.files && <ul className="max-h-48 space-y-1 overflow-auto">{result.files.map(file => <li key={file.fs_id} className="break-all">{displayPath(file.path)}</li>)}</ul>}
        <details><summary className="cursor-pointer text-slate-500">查看操作结果</summary><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(result, null, 2)}</pre></details>
      </div>}
      <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={onClose}>{result || doneUploading ? '关闭' : '取消'}</button>{canSubmit && <button type="submit" className={`inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm text-white disabled:opacity-50 ${tool === 'delete' ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'}`} disabled={busy}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}{tool === 'delete' ? '确认删除' : tool === 'semantic-search' ? '开始搜索' : tool === 'upload' ? '确认上传' : tool === 'download' ? <><Download className="h-4 w-4" />开始下载</> : tool === 'share-transfer' ? '确认转存' : '确认执行'}</button>}</div>
    </form>}
  </QuarkDialog>
}
