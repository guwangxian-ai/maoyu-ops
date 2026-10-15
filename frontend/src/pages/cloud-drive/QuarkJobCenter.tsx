import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Loader2, RefreshCw } from 'lucide-react'
import { cancelQuarkJob, downloadQuarkJobFile, executeQuarkPlan, followupQuarkJob, getQuarkJobs, runQuarkOperation, type QuarkJob } from '@/api/quarkDrive'
import { ConfirmModal } from '@/components/common/ConfirmModal'

const statusNames: Record<string, string> = { planned: '方案待确认', queued: '等待执行', running: '执行中', succeeded: '完成', partial: '部分完成', failed: '失败', needs_confirmation: '需要确认', interrupted: '已中断', unknown: '结果待核验', submitted: '已提交确认' }
export const operationNames: Record<string, string> = { upload: '上传', download: '下载', 'create-folder': '新建文件夹', move: '移动', rename: '重命名', 'rename-revert': '撤销重命名', summary: '文件总结', qa: '文件问答', organize: '相册整理', 'organize-copy': '确认复制整理', 'organize-move': '确认移动整理', saveas: '转存分享', 'saveas-update': '转存更新', 'upload-resume': '恢复上传', 'download-resume': '恢复下载', 'upload-delete': '删除上传记录', 'download-delete': '删除下载记录', 'batch-organize': '批量整理', 'batch-organize-resume': '恢复批量整理', 'batch-organize-undo': '撤回批量整理' }
const buttonClass = 'inline-flex items-center gap-1.5 rounded-md border border-slate-200 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800'
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const safeUrl = (value: unknown) => {
  if (typeof value !== 'string') return ''
  try { const url = new URL(value); return url.protocol === 'https:' && url.hostname === 'pan.quark.cn' ? value : '' } catch { return '' }
}

function JobResult({ job }: { job: QuarkJob }) {
  const data = job.result?.data || {}
  const text = object(data.text_block)
  const link = safeUrl(data.result_page_url || data.detail_url || data.checkAllLink)
  const counts = [['计划', data.total], ['成功', data.success_count ?? data.successCount], ['失败', data.fail_count ?? data.failCount], ['待确认', data.unknown_count], ['待处理', data.pending_count], ['整理文件', data.total_file_count]] as const
  const proposal = job.preview?.proposal || {}
  const folders = Array.isArray(proposal.new_folders) ? proposal.new_folders.map(object) : []
  const operations = Array.isArray(proposal.operations) ? proposal.operations.map(object) : []
  return <div className="space-y-3">
    {job.preview?.items && <div className="max-h-72 overflow-auto rounded border border-slate-200 dark:border-slate-700"><table className="w-full text-left text-sm"><thead><tr><th className="p-2">原名称</th><th className="p-2">新名称</th></tr></thead><tbody>{job.preview.items.map(item => <tr key={item.fid}><td className="break-all p-2">{item.old_name}</td><td className="break-all p-2">{item.new_name}</td></tr>)}</tbody></table></div>}
    {job.preview?.summary && <p className="text-sm text-slate-500">涉及 {String(job.preview.summary.affected_object_count ?? '—')} 项，改名 {String(job.preview.summary.rename_count ?? '—')} 项，目标目录 {String(job.preview.summary.new_folder_count ?? '—')} 个。</p>}
    {job.preview?.target_label && <p className="break-all text-sm">目标：{job.preview.target_label}</p>}
    {folders.length > 0 && <div className="space-y-1 text-sm"><h4 className="font-medium">目标目录结构</h4>{folders.map((folder, index) => <p key={index} className="break-all">{String(folder.path)}</p>)}<p className="text-slate-500">共 {String(job.preview?.summary?.affected_object_count ?? '—')} 项 · 目录结构 {Math.max(...folders.map(folder => String(folder.path).split('/').length))} 层 · {folders.length} 个目录声明</p></div>}
    {operations.length > 0 && <div className="max-h-72 space-y-3 overflow-auto text-sm">{operations.map((operation, index) => <div key={index}><p className="break-all text-slate-500">移动到：{String(folders.find(folder => folder.plan_id === operation.to)?.path || job.preview?.target_label || '方案指定的已有目录')}</p>{(Array.isArray(operation.items) ? operation.items.map(object) : []).map((item, row) => <p key={row} className="break-all">{String(item.origin_name)}{item.new_name ? ` → ${String(item.new_name)}` : ''}</p>)}</div>)}</div>}
    <div className="flex flex-wrap gap-4 text-sm">{counts.filter(([, value]) => typeof value === 'number').map(([label, value]) => <span key={label}>{label}：{String(value)}</span>)}</div>
    {typeof text.text === 'string' && <div className="whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-4 text-sm leading-7 dark:bg-slate-800">{typeof text.title === 'string' && <h4 className="mb-2 font-semibold">{text.title}</h4>}{text.text}</div>}
    {link && job.operation !== 'rename-revert' && <a href={link} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-600 underline">查看夸克结果页面</a>}
    {job.operation === 'rename' && ['succeeded', 'partial'].includes(job.status) && <p className="text-xs text-slate-500">可在原操作的 30 天撤回期限内撤销本次重命名，实际有效期以夸克判断为准。</p>}
    {job.operation.startsWith('batch-organize') && !job.operation.includes('undo') && ['succeeded', 'partial'].includes(job.status) && <p className="text-xs text-slate-500">30 天内可撤回本次整理，恢复文件位置和名称。撤回期限以夸克判断为准，服务端的原整理记录需保留。</p>}
  </div>
}

export function QuarkJobCenter({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const [jobs, setJobs] = useState<QuarkJob[]>([])
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [confirmation, setConfirmation] = useState<{ text: string; action: () => Promise<void> } | null>(null)
  const seenJobs = useRef<Map<string, string> | null>(null)
  const changed = useRef(onChanged)
  changed.current = onChanged

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const result = await getQuarkJobs(offset)
        if (!active) return
        if (!result.success || !result.data) throw new Error(result.message || '读取任务失败')
        const next = result.data.jobs
        if (seenJobs.current && next.some(job => seenJobs.current!.get(job.id) !== job.status && !['queued', 'running', 'planned'].includes(job.status))) changed.current()
        seenJobs.current = new Map(next.map(job => [job.id, job.status]))
        setJobs(next); setHasMore(result.data.has_more); setError('')
        if (next.some(job => ['queued', 'running'].includes(job.status)) || offset > 0) timer = setTimeout(() => void load(), 5000)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : '读取任务失败')
      } finally { if (active) setLoading(false) }
    }
    setLoading(true); void load()
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [refreshKey, refresh, offset])

  const run = useCallback(async (operation: () => Promise<{ success: boolean; message?: string }>) => {
    const result = await operation()
    if (!result.success) throw new Error(result.message || '提交任务失败')
    setRefresh(value => value + 1)
  }, [])

  const saveFile = async (job: QuarkJob, file: QuarkJob['files'][number]) => {
    setError('')
    try {
      const blob = await downloadQuarkJobFile(job.id, file.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = file.name; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '获取下载文件失败') }
  }

  return <section id="quark-job-center" className="space-y-3 border-t border-slate-200 pt-5 dark:border-slate-700">
    <div className="flex items-center justify-between"><h2 className="font-semibold">任务中心</h2><button type="button" className={buttonClass} onClick={() => setRefresh(value => value + 1)}><RefreshCw className="h-4 w-4" />刷新任务</button></div>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {loading && jobs.length === 0 ? <Loader2 className="h-5 w-5 animate-spin text-blue-500" /> : jobs.length === 0 ? <p className="py-6 text-center text-sm text-slate-500">暂无任务。执行上传、下载或其他操作后，进度和结果会保存在这里。</p> : <div className="space-y-3">{jobs.map(job => {
      const progress = Number(job.progress?.data?.percent)
      const active = ['queued', 'running'].includes(job.status)
      return <div key={job.id} className="space-y-3 rounded-lg border border-slate-200 p-4 dark:border-slate-700">
        <div className="flex flex-wrap items-start justify-between gap-2"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">{operationNames[job.operation] || job.operation}</h3><span className={`rounded px-2 py-0.5 text-xs ${job.status === 'failed' ? 'bg-red-50 text-red-600' : active ? 'bg-blue-50 text-blue-600' : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300'}`}>{statusNames[job.status] || job.status}</span></div><p className="mt-1 text-xs text-slate-400">{new Date(job.created_at).toLocaleString('zh-CN')}</p></div><button className="text-sm text-blue-600" type="button" onClick={() => setExpanded(value => value === job.id ? '' : job.id)}>{expanded === job.id ? '收起' : '查看详情'}</button></div>
        <p className="whitespace-pre-wrap break-all text-sm text-slate-600 dark:text-slate-300">{active ? job.progress?.message || job.message : job.message}</p>
        {active && Number.isFinite(progress) && <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"><div className="h-full bg-blue-500" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div>}
        {expanded === job.id && <JobResult job={job} />}
        <div className="flex flex-wrap gap-2">
          {job.status === 'planned' && <button type="button" className={buttonClass} onClick={() => { setExpanded(job.id); setConfirmation({ text: '请核对方案中的文件名称和目标目录。确认后会执行真实改名或移动。', action: () => run(() => executeQuarkPlan(job.id)) }) }}>确认执行方案</button>}
          {active && <button type="button" className={buttonClass} onClick={() => setConfirmation({ text: '停止等待当前任务。已完成或已提交的操作不会自动撤回，传输断点可在传输记录中恢复。', action: () => run(() => cancelQuarkJob(job.id)) })}>停止任务</button>}
          {['rename', 'rename-resume'].includes(job.operation) && ['succeeded', 'partial'].includes(job.status) && <button type="button" className={buttonClass} onClick={() => setConfirmation({ text: '撤销此批次已经生效的重命名，恢复原文件名称。', action: () => run(() => followupQuarkJob(job.id, 'revert')) })}>撤销重命名</button>}
          {['rename', 'rename-resume'].includes(job.operation) && ['failed', 'interrupted', 'unknown'].includes(job.status) && job.result?.code !== 0 && <button type="button" className={buttonClass} onClick={() => setConfirmation({ text: '使用原批次编号和改名方案查询或接续任务，避免重复改名。', action: () => run(() => followupQuarkJob(job.id, 'rename-resume')) })}>接续原批次</button>}
          {job.operation.startsWith('batch-organize') && !active && job.status !== 'planned' && <>
            {['failed', 'interrupted', 'unknown'].includes(job.status) && <button type="button" className={buttonClass} onClick={() => setConfirmation({ text: '使用原记录接续当前整理任务。已经完成的步骤由夸克 CLI 记录处理。', action: () => run(() => followupQuarkJob(job.id, job.operation.includes('undo') ? 'undo-resume' : 'resume')) })}>接续原任务</button>}
            {!job.operation.includes('undo') && <button type="button" className={buttonClass} onClick={() => setConfirmation({ text: '撤回本次整理中的移动和改名，恢复原位置与名称；有效期以夸克返回结果为准。', action: () => run(() => followupQuarkJob(job.id, 'undo')) })}>撤回整理</button>}
          </>}
          {job.status === 'needs_confirmation' && typeof job.result?.data?.task_id === 'string' && (['copy', 'move'] as const).map(way => <button type="button" key={way} className={buttonClass} onClick={() => setConfirmation({ text: `${way === 'copy' ? '复制文件副本' : '移动原文件'}完成相册整理。`, action: () => run(() => runQuarkOperation(`organize-${way}`, { task_id: String(job.result?.data?.task_id), confirmed: true })) })}>{way === 'copy' ? '复制整理' : '移动整理'}</button>)}
          {job.files.map(file => <button type="button" className={buttonClass} key={file.id} onClick={() => void saveFile(job, file)}><Download className="h-4 w-4" />保存 {file.name}</button>)}
        </div>
      </div>
    })}</div>}
    {(offset > 0 || hasMore) && <div className="flex justify-end gap-2"><button type="button" className={buttonClass} disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页</button><button type="button" className={buttonClass} disabled={!hasMore} onClick={() => setOffset(value => value + 50)}>下一页</button></div>}
    <ConfirmModal isOpen={!!confirmation} title="确认夸克操作" message={confirmation?.text || ''} loading={busy} onCancel={() => { if (!busy) setConfirmation(null) }} onConfirm={() => {
      if (!confirmation || busy) return
      setBusy(true); setError(''); confirmation.action().then(() => setConfirmation(null)).catch(reason => { setConfirmation(null); setError(reason instanceof Error ? reason.message : '提交失败') }).finally(() => setBusy(false))
    }} />
  </section>
}
