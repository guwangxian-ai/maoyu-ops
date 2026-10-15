import { useEffect, useState } from 'react'
import { runQuarkOperation, uploadQuarkFiles, type QuarkOperationPayload } from '@/api/quarkDrive'
import { QuarkDialog, quarkButton, quarkError, quarkObject } from './QuarkTools'

const states: Record<string, string> = { pending: '等待', hashing: '计算校验', uploading: '上传中', paused: '已暂停', success: '完成', failed: '失败', cancelled: '已取消', post_hashing: '校验中', completed: '完成', downloading: '下载中' }
export function QuarkTransfers({ onJob }: { onJob: () => void }) {
  const [kind, setKind] = useState<'upload' | 'download'>('upload')
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState<{ operation: string; payload: QuarkOperationPayload; title: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [replacement, setReplacement] = useState<File | null>(null)
  const [filter, setFilter] = useState('')
  useEffect(() => {
    let active = true
    setLoading(true); setRows([]); setError('')
    runQuarkOperation(`${kind}-list`, filter ? { state: filter } : {}).then(result => {
      if (!active) return
      if (!result.success || !result.data) throw new Error(result.message || '读取传输记录失败')
      setRows((result.data.items || []).map(quarkObject))
    }).catch(reason => { if (active) setError(quarkError(reason)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [kind, refresh, filter])
  const submit = async () => {
    if (!draft) return
    setBusy(true); setError('')
    try {
      const result = draft.operation === 'upload-resume' && replacement ? await uploadQuarkFiles([replacement], '0', draft.payload.record_id) : await runQuarkOperation(draft.operation, draft.payload)
      if (!result.success) throw new Error(result.message || '提交失败')
      setDraft(null); setReplacement(null); setNotice('任务已提交，请在任务中心查看结果。完成后刷新传输记录。'); onJob()
    } catch (reason) { setError(quarkError(reason)) } finally { setBusy(false) }
  }
  return <div className="space-y-4"><div className="flex flex-wrap gap-2"><button type="button" className={quarkButton} disabled={kind === 'upload'} onClick={() => { setKind('upload'); setFilter('') }}>上传记录</button><button type="button" className={quarkButton} disabled={kind === 'download'} onClick={() => { setKind('download'); setFilter('') }}>下载记录</button><select aria-label="传输状态筛选" className={`${quarkButton} bg-white dark:bg-slate-800`} value={filter} onChange={event => setFilter(event.target.value)}><option value="">全部状态</option>{(kind === 'upload' ? ['pending', 'hashing', 'uploading', 'paused', 'success', 'failed', 'cancelled', 'post_hashing'] : ['pending', 'downloading', 'paused', 'completed', 'failed', 'cancelled']).map(state => <option key={state} value={state}>{states[state]}</option>)}</select><button type="button" className={quarkButton} disabled={loading} onClick={() => setRefresh(value => value + 1)}>刷新记录</button></div>
    <p className="text-sm text-slate-500">记录保存在服务端，可从断点恢复。删除记录仅清理传输任务；云端文件不受影响。恢复上传需要原暂存文件仍然存在。</p>
    {notice && <p role="status" className="text-sm text-green-700">{notice}</p>}{error && !draft && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {loading ? <p className="text-sm text-blue-600">正在读取…</p> : rows.length === 0 ? <p className="py-6 text-center text-sm text-slate-500">暂无传输记录</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="p-3">文件</th><th className="p-3">状态</th><th className="p-3">大小</th><th className="p-3">操作</th></tr></thead><tbody>{rows.map((row, index) => {
      const id = String(row.recordId || row.record_id || '')
      return <tr key={id || index} className="border-t border-slate-200 dark:border-slate-700"><td className="break-all p-3">{String(row.fileName || row.filename || '文件')}</td><td className="p-3">{states[String(row.state)] || String(row.state || '—')}</td><td className="whitespace-nowrap p-3">{typeof row.fileSize === 'number' ? `${(row.fileSize / 1024**2).toFixed(2)} MB` : '—'}</td><td className="p-3"><div className="flex gap-3">{!['success', 'completed'].includes(String(row.state)) && <button type="button" disabled={!id} className="text-blue-600" onClick={() => { setError(''); setDraft({ operation: `${kind}-resume`, title: `恢复${kind === 'upload' ? '上传' : '下载'}`, payload: { record_id: id, confirmed: true } }) }}>恢复</button>}<button type="button" disabled={!id} className="text-red-600" onClick={() => { setError(''); setDraft({ operation: `${kind}-delete`, title: '删除传输记录', payload: { record_id: id, confirmed: true } }) }}>删除记录</button></div></td></tr>
    })}</tbody></table></div>}
    {draft && <QuarkDialog title={draft.title} busy={busy} error={error} onClose={() => { setDraft(null); setReplacement(null) }}><p className="text-sm">{draft.operation.endsWith('delete') ? '删除后，这条传输记录的断点将不可恢复。已上传的云端文件或已下载的文件不会因此删除。' : '使用官方保存的原任务断点继续传输。进度和结果会保存到任务中心。'}</p>{draft.operation === 'upload-resume' && <label className="block space-y-2 text-sm"><span>若原暂存文件已清理，重新选择同一份源文件（可选）</span><input type="file" onChange={event => setReplacement(event.target.files?.[0] || null)} /></label>}<div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={() => { setDraft(null); setReplacement(null) }}>取消</button><button type="button" className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white" onClick={() => void submit()}>确认操作</button></div></QuarkDialog>}
  </div>
}
