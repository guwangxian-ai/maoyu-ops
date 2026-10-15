import { useState } from 'react'
import { runQuarkOperation, type QuarkOperationPayload } from '@/api/quarkDrive'
import { QuarkFolderPicker } from './QuarkFolderPicker'
import { QuarkDialog, quarkButton, quarkError, quarkInput, quarkObject } from './QuarkTools'

const rowsOf = (value: unknown) => Array.isArray(value) ? value.map(quarkObject) : []
type Mode = 'share-detail' | 'share-search' | 'get-share-update-files' | 'get-share-update-list'
const nameOf = (file: Record<string, unknown>) => String(file.filename || file.title || '未命名文件')
const withPasscode = (value: string, passcode: string) => {
  try { const url = new URL(value); if (passcode) url.searchParams.set('pwd', passcode); return url.toString() } catch { return value }
}

export function QuarkShareTools({ onJob }: { onJob: () => void }) {
  const [url, setUrl] = useState('')
  const [passcode, setPasscode] = useState('')
  const [keyword, setKeyword] = useState('')
  const [loadedUrl, setLoadedUrl] = useState('')
  const [mode, setMode] = useState<Mode>('share-detail')
  const [page, setPage] = useState(1)
  const [path, setPath] = useState([{ fid: '0', name: '分享根目录' }])
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [meta, setMeta] = useState<Record<string, unknown>>({})
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [savedDir, setSavedDir] = useState('')
  const [draft, setDraft] = useState<{ operation: string; payload: QuarkOperationPayload; title: string } | null>(null)
  const [target, setTarget] = useState<{ fid: string; name: string } | null>(null)
  const [picking, setPicking] = useState(false)

  const query = async (nextMode: Mode, nextPage = 1, nextPath = [{ fid: '0', name: '分享根目录' }], explicitUrl?: string) => {
    setBusy(true); setError(''); setMessage(''); setRows([]); setSelected([]); setSavedDir(''); setLoadedUrl(''); setMeta({})
    const source = explicitUrl || withPasscode(url.trim(), passcode.trim())
    try {
      const result = await runQuarkOperation(nextMode, { ...(nextMode !== 'get-share-update-list' ? { url: source, passcode: passcode.trim() } : {}), page: nextPage, size: 50,
        ...(nextMode === 'share-search' ? { keyword: keyword.trim() } : {}), ...(nextMode === 'share-detail' ? { parent_fid: nextPath[nextPath.length - 1].fid } : {}) })
      if (!result.success || !result.data) throw new Error(result.message || '查询失败')
      const data = result.data.data || {}
      setMode(nextMode); setPage(nextPage); setPath(nextPath); setMeta(data)
      setLoadedUrl(String(data.share_url || source))
      setRows(nextMode === 'get-share-update-list' ? rowsOf(result.data.items) : rowsOf(data.files))
      setMessage(result.data.message || '查询成功')
    } catch (reason) { setError(quarkError(reason)) } finally { setBusy(false) }
  }
  const saved = async () => {
    setBusy(true); setError('')
    try {
      const result = await runQuarkOperation('get-share-saved-dir', { url: loadedUrl || withPasscode(url, passcode) })
      if (!result.success || !result.data) throw new Error(result.message || '读取转存目录失败')
      const data = result.data.data || {}
      setSavedDir(String(data.pdir_name || data.dir_name || data.full_path || data.path || result.data.message || '已查询转存目录'))
    } catch (reason) { setError(quarkError(reason)) } finally { setBusy(false) }
  }
  const transfer = (all: boolean) => {
    setError(''); setTarget(null); setPicking(false)
    const updates = mode === 'get-share-update-files'
    setDraft({ operation: updates && all ? 'saveas-update' : 'saveas', title: updates ? all ? '转存全部新增文件' : `转存选中的 ${selected.length} 个更新文件` : all ? '转存整个分享' : `转存选中的 ${selected.length} 项`, payload: { url: loadedUrl || withPasscode(url, passcode), passcode,
      ...(!all ? { fids: selected } : {}), ...(updates && !all ? { use_saved_directory: true } : {}), confirmed: true } })
  }
  const submit = async () => {
    if (!draft) return
    setBusy(true); setError('')
    try {
      const result = await runQuarkOperation(draft.operation, { ...draft.payload, ...(target && !draft.payload.use_saved_directory && draft.operation !== 'saveas-update' ? { target_fid: target.fid } : {}) })
      if (!result.success) throw new Error(result.message || '转存失败')
      setDraft(null); setRows([]); setSelected([]); setLoadedUrl(''); setMeta({}); setMessage('转存任务已提交，请在任务中心查看结果。再次转存前请重新查询。'); onJob()
    } catch (reason) { setError(quarkError(reason)) } finally { setBusy(false) }
  }
  const more = typeof meta.has_next_page === 'boolean' ? meta.has_next_page : rows.length >= 50
  const clear = () => { setRows([]); setSelected([]); setLoadedUrl(''); setMeta({}); setSavedDir(''); setMessage('') }
  return <div className="space-y-4">
    <p className="text-sm text-slate-500">查看分享中的文件，转存所选内容，或查询已转存分享的新增文件。每次翻页由你选择。</p>
    <fieldset disabled={busy} className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row"><input aria-label="夸克分享链接" className={`${quarkInput} flex-1`} placeholder="https://pan.quark.cn/s/…" value={url} onChange={event => { setUrl(event.target.value); clear() }} /><input aria-label="分享提取码" className={`${quarkInput} sm:w-32`} placeholder="提取码（可选）" maxLength={32} value={passcode} onChange={event => { setPasscode(event.target.value); clear() }} /></div>
      <div className="flex flex-wrap gap-2"><button type="button" className={quarkButton} disabled={!url.trim()} onClick={() => void query('share-detail')}>查看分享文件</button><button type="button" className={quarkButton} disabled={!url.trim()} onClick={() => void query('get-share-update-files')}>查看新增文件</button><button type="button" className={quarkButton} onClick={() => void query('get-share-update-list')}>查询有更新的转存分享</button><button type="button" className={quarkButton} disabled={!url.trim()} onClick={() => void saved()}>查看上次转存目录</button></div>
      <form className="flex gap-2" onSubmit={event => { event.preventDefault(); void query('share-search') }}><input aria-label="搜索分享内容" maxLength={50} className={quarkInput} placeholder="搜索分享中的文件名称" value={keyword} onChange={event => setKeyword(event.target.value)} /><button type="submit" className={`${quarkButton} whitespace-nowrap`} disabled={!url.trim() || !keyword.trim()}>搜索分享</button></form>
    </fieldset>
    {error && !draft && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {busy && !draft && <p role="status" className="text-sm text-blue-600">正在查询夸克…</p>}
    {message && <p role="status" className="whitespace-pre-wrap break-all text-sm text-slate-600 dark:text-slate-300">{message}</p>}
    {savedDir && <p className="break-all text-sm">上次转存目录：{savedDir}</p>}
    {mode === 'share-detail' && path.length > 1 && <div className="flex flex-wrap gap-2 text-sm">{path.map((folder, index) => <button key={index} type="button" disabled={busy || index === path.length - 1} className="text-blue-600 disabled:text-slate-500" onClick={() => void query('share-detail', 1, path.slice(0, index + 1), loadedUrl)}>{folder.name} /</button>)}</div>}
    {rows.length > 0 && <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-slate-500 dark:bg-slate-800"><tr>{mode !== 'get-share-update-list' && <th className="p-3">选择</th>}<th className="p-3">{mode === 'get-share-update-list' ? '分享标题' : '文件名'}</th><th className="p-3">{mode === 'get-share-update-list' ? '更新时间' : '大小'}</th><th className="p-3">{mode === 'get-share-update-list' ? '操作' : '文件类型'}</th></tr></thead><tbody>{rows.map((row, index) => {
      const fid = String(row.fid || '')
      const folder = row.file_type === '0' || row.category === 0
      return <tr key={fid || index} className="border-t border-slate-100 dark:border-slate-700">{mode !== 'get-share-update-list' && <td className="p-3"><input aria-label={`选择 ${nameOf(row)}`} type="checkbox" checked={selected.includes(fid)} disabled={busy || !fid || (!selected.includes(fid) && selected.length >= 100)} onChange={event => setSelected(values => event.target.checked ? [...values, fid] : values.filter(value => value !== fid))} /></td>}
        <td className="break-all p-3">{folder && mode === 'share-detail' ? <button type="button" disabled={busy} className="text-blue-600" onClick={() => void query('share-detail', 1, [...path, { fid, name: nameOf(row) }], loadedUrl)}>{nameOf(row)}</button> : nameOf(row)}</td>
        <td className="whitespace-nowrap p-3 text-slate-500">{mode === 'get-share-update-list' ? String(row.updated_at || '—') : typeof row.size === 'number' ? `${(row.size / 1024**2).toFixed(2)} MB` : '—'}</td>
        <td className="p-3">{mode === 'get-share-update-list' ? <button type="button" className="text-blue-600" disabled={busy} onClick={() => { setUrl(String(row.share_url || '')); setPasscode(String(row.passcode || '')); void query('get-share-update-files', 1, undefined, withPasscode(String(row.share_url || ''), String(row.passcode || ''))) }}>查看更新</button> : folder ? '文件夹' : String(row.file_type === '1' ? '文件' : row.file_type || '文件')}</td>
      </tr>
    })}</tbody></table></div>}
    {(rows.length > 0 || loadedUrl) && <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap gap-2">{mode !== 'get-share-update-list' && <><button type="button" className={quarkButton} disabled={busy || selected.length === 0} onClick={() => transfer(false)}>转存所选（{selected.length}）</button><button type="button" className={quarkButton} disabled={busy || !loadedUrl || (mode === 'get-share-update-files' && rows.length === 0)} onClick={() => transfer(true)}>{mode === 'get-share-update-files' ? '转存全部新增文件' : '转存整个分享'}</button></>}</div><div className="flex items-center gap-2 text-sm"><button type="button" className={quarkButton} disabled={busy || page <= 1} onClick={() => void query(mode, page - 1, path, loadedUrl)}>上一页</button><span>第 {page} 页{typeof meta.total === 'number' ? ` · 共 ${meta.total} 项` : ''}</span><button type="button" className={quarkButton} disabled={busy || !more} onClick={() => void query(mode, page + 1, path, loadedUrl)}>下一页</button></div></div>}
    {draft && <QuarkDialog title={draft.title} busy={busy} error={error} onClose={() => setDraft(null)}><p className="break-all text-sm">分享：{draft.payload.url}</p>
      {draft.operation === 'saveas-update' || draft.payload.use_saved_directory ? <p className="text-sm text-slate-500">使用上次转存的目录。只转存所选更新时，若官方未返回原目录，则使用官方默认转存位置。</p> : <><p className="text-sm text-slate-500">转存目标：{target?.name || '夸克默认转存位置'}</p><button type="button" className={quarkButton} onClick={() => setPicking(value => !value)}>选择目标目录</button>{picking && <QuarkFolderPicker onSelect={(fid, name) => { setTarget({ fid, name }); setPicking(false) }} />}{target && <button type="button" className="ml-3 text-sm text-blue-600" onClick={() => setTarget(null)}>使用默认位置</button>}</>}
      <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={() => setDraft(null)}>取消</button><button type="button" className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white" onClick={() => void submit()}>确认转存</button></div>
    </QuarkDialog>}
  </div>
}
