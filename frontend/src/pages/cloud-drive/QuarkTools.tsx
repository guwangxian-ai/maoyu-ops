import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Download, FolderPlus, Link2, Loader2, MoreHorizontal, Move, Pencil, Upload, X } from 'lucide-react'
import { executeQuarkPlan, prepareQuarkOrganize, prepareQuarkRename, runQuarkOperation, uploadQuarkFiles, type QuarkFile, type QuarkJob } from '@/api/quarkDrive'
import { QuarkFolderPicker } from './QuarkFolderPicker'

export const quarkButton = 'inline-flex items-center justify-center gap-1.5 rounded-md border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800'
export const quarkInput = 'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900'
export const quarkObject = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
export const quarkError = (value: unknown) => value instanceof Error ? value.message : '请求失败，请重试'

export function QuarkDialog({ title, busy, error, onClose, children, compact = false }: { title: string; busy: boolean; error?: string; onClose: () => void; children: ReactNode; compact?: boolean }) {
  const dialog = useRef<HTMLElement>(null)
  const state = useRef({ onClose, busy })
  state.current = { onClose, busy }
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusable = () => Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]') || []).filter(item => item.offsetParent !== null)
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !state.current.busy) state.current.onClose()
      if (event.key !== 'Tab') return
      const items = focusable()
      if (!items.length) { event.preventDefault(); dialog.current?.focus(); return }
      const next = event.shiftKey ? items[items.length - 1] : items[0]
      if (!dialog.current?.contains(document.activeElement) || document.activeElement === (event.shiftKey ? items[0] : items[items.length - 1])) { event.preventDefault(); next.focus() }
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    ;(focusable()[0] || dialog.current)?.focus()
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); document.body.style.overflow = previousOverflow; previous?.focus() }
  }, [])
  return <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/35 p-4"><section ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={`max-h-[90dvh] w-full ${compact ? 'max-w-lg' : 'max-w-3xl'} space-y-5 overflow-y-auto rounded-xl border border-slate-200 bg-white p-6 text-slate-800 shadow-xl outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100`}>
    <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">{title}</h2><button type="button" aria-label="关闭操作窗口" disabled={busy} onClick={onClose} className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 dark:hover:bg-slate-700"><X className="h-5 w-5" /></button></div>
    <fieldset disabled={busy} className="min-w-0 space-y-4">{children}</fieldset>
    {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    {busy && <p role="status" className="flex items-center gap-2 text-sm text-blue-600"><Loader2 className="h-4 w-4 animate-spin" />正在提交，请等待结果…</p>}
  </section></div>
}

export type QuarkTool = 'create-folder' | 'upload' | 'move' | 'download' | 'rename' | 'batch-organize' | 'summary' | 'qa' | 'organize'
type Tool = QuarkTool
const labels: Record<Tool, string> = { 'create-folder': '新建文件夹', upload: '上传文件 / 文件夹', move: '移动所选', download: '下载所选', rename: '重命名', 'batch-organize': '批量整理', summary: '文件总结', qa: '文件问答', organize: '智能相册整理' }

export function QuarkTools({ files, parentFid, folderName, onJob, onShare, onTransfer, trigger, toolbar, accountControl }: { files: QuarkFile[]; parentFid: string; folderName: string; onJob: () => void; onShare?: () => void; onTransfer?: () => void; trigger?: { tool: Tool; files: QuarkFile[]; id: number } | null; toolbar?: ReactNode; accountControl?: ReactNode }) {
  const [tool, setTool] = useState<Tool | null>(null)
  const [chosen, setChosen] = useState<QuarkFile[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [name, setName] = useState('')
  const [query, setQuery] = useState('')
  const [target, setTarget] = useState<{ fid: string; name: string } | null>(null)
  const [picking, setPicking] = useState(false)
  const [newFolder, setNewFolder] = useState(false)
  const [newNames, setNewNames] = useState<string[]>([])
  const [uploads, setUploads] = useState<File[]>([])
  const [plan, setPlan] = useState<QuarkJob | null>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const open = (value: Tool, selectedFiles = files) => {
    setTool(value); setChosen(selectedFiles); setMoreOpen(false); setError(''); setName(''); setQuery(value === 'summary' ? '总结所选文件的主要内容和关键结论' : ''); setTarget(null); setPicking(false); setNewFolder(false); setNewNames(selectedFiles.map(file => file.filename || '')); setUploads([]); setPlan(null)
  }
  const triggerRef = useRef(open)
  triggerRef.current = open
  useEffect(() => { if (trigger) triggerRef.current(trigger.tool, trigger.files) }, [trigger])
  const submit = async () => {
    if (!tool || busy) return
    setBusy(true); setError('')
    try {
      if (tool === 'rename' || tool === 'batch-organize') {
        if (plan) {
          const result = await executeQuarkPlan(plan.id)
          if (!result.success) throw new Error(result.message || '执行方案失败')
        } else {
          const result = tool === 'rename' ? await prepareQuarkRename(chosen.map((file, index) => ({ fid: file.fid, parent_fid: file.parent_fid || parentFid, old_name: file.filename || '', new_name: newNames[index] }))) : await prepareQuarkOrganize({ version: 4,
            new_folders: newFolder ? [{ plan_id: 's_destination', parent_fid: target!.fid, path: name.trim() }] : [],
            operations: [{ op: 'move', to: newFolder ? 's_destination' : target!.fid, items: chosen.map((file, index) => ({ fid: file.fid, parent_fid: file.parent_fid || parentFid, origin_name: file.filename, ...(newNames[index] !== file.filename ? { new_name: newNames[index] } : {}) })) }] }, `${target!.name}${newFolder ? ` / ${name.trim()}` : ''}`)
          if (!result.success || !result.data) throw new Error(result.message || '生成方案失败')
          setPlan(result.data.job); onJob(); return
        }
      } else if (tool === 'upload') {
        const result = await uploadQuarkFiles(uploads, parentFid)
        if (!result.success) throw new Error(result.message || '上传失败')
      } else {
        const result = await runQuarkOperation(tool, { confirmed: true,
          ...(tool === 'create-folder' ? { name, parent_fid: parentFid } : {}),
          ...(['move', 'download', 'summary', 'qa'].includes(tool) ? { fids: chosen.map(file => file.fid) } : {}),
          ...(tool === 'move' ? { target_fid: target!.fid } : {}),
          ...(['summary', 'qa', 'organize'].includes(tool) ? { query } : {}) })
        if (!result.success) throw new Error(result.message || '提交失败')
      }
      setTool(null); setNotice('任务已提交，进度与结果可在「任务中心」查看。'); onJob()
    } catch (reason) { setError(quarkError(reason)) } finally { setBusy(false) }
  }
  const requiresSelection = (value: Tool) => !['create-folder', 'upload', 'organize'].includes(value)
  const disabled = busy || !tool || (requiresSelection(tool) && chosen.length === 0)
    || (tool === 'create-folder' && !name.trim()) || (tool === 'upload' && uploads.length === 0)
    || (['move', 'batch-organize'].includes(tool) && !target) || (tool === 'batch-organize' && newFolder && !name.trim())
    || (['summary', 'qa', 'organize'].includes(tool) && !query.trim())
    || (['rename', 'batch-organize'].includes(tool) && newNames.some(value => !value))

  return <div className="space-y-3">
    <div aria-label="夸克网盘工具栏" className={`grid items-start gap-3 ${accountControl ? 'grid-cols-[minmax(0,1fr)_auto]' : 'grid-cols-1'}`}>
    <div className="flex min-w-0 flex-wrap items-center gap-3"><div className="flex flex-wrap items-center gap-2">
      <button type="button" className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500" onClick={() => open('upload')}><Upload className="h-4 w-4" />上传文件</button>
      <button type="button" className={quarkButton} onClick={() => open('create-folder')}><FolderPlus className="h-4 w-4" />新建文件夹</button>
      {onTransfer && <button type="button" className={quarkButton} onClick={onTransfer}><Link2 className="h-4 w-4" />转存链接</button>}
      <button type="button" className="px-1 text-xs text-slate-500 hover:text-blue-600" onClick={() => open('organize')}>智能整理</button>
    </div>{toolbar}</div>
    {accountControl && <div className="justify-self-end">{accountControl}</div>}
    </div>
    {files.length > 0 && <div className="flex flex-wrap items-center gap-2 rounded-md bg-blue-50 px-3 py-2 dark:bg-blue-950/40">
      <span className="mr-2 text-sm text-slate-600 dark:text-slate-300">已选择 {files.length} 项</span>
      {onShare && <button type="button" className={quarkButton} onClick={onShare}>分享</button>}
      {([['download', Download], ['move', Move], ['rename', Pencil]] as const).map(([value, Icon]) => <button type="button" key={value} className={quarkButton} disabled={value === 'download' && files.some(file => file.file_type === '0' || file.category === 0)} title={value === 'download' ? '文件夹请先打开后选择其中的文件' : undefined} onClick={() => open(value)}><Icon className="h-4 w-4" />{value === 'download' ? '下载' : value === 'move' ? '移动' : '重命名'}</button>)}
      <details open={moreOpen} onToggle={event => setMoreOpen(event.currentTarget.open)} className="relative"><summary className={`${quarkButton} cursor-pointer list-none`}><MoreHorizontal className="h-4 w-4" />更多</summary><div className="absolute left-0 top-full z-20 mt-2 w-44 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">{(['batch-organize', 'summary', 'qa'] as const).map(value => <button type="button" key={value} className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-700" onClick={() => open(value)}>{labels[value]}</button>)}</div></details>
    </div>}
    {notice && <p role="status" className="text-sm text-green-700 dark:text-green-400">{notice}</p>}
    {tool && <QuarkDialog title={labels[tool]} busy={busy} error={error} onClose={() => setTool(null)}>
      <p className="text-sm text-slate-500">{requiresSelection(tool) ? `已选择 ${chosen.length} 项。` : `当前目录：${folderName}。`}{tool === 'download' && '先下载到服务端，再在任务中心保存到你的电脑。'}</p>
      {tool === 'create-folder' && <label className="block space-y-2 text-sm"><span>名称（可用 / 创建多级目录）</span><input autoFocus className={quarkInput} maxLength={255} value={name} onChange={event => setName(event.target.value)} placeholder="例如：商品资料 / 课程" /></label>}
      {tool === 'upload' && <>
        <div className="flex flex-wrap gap-2"><label className={quarkButton}>选择文件<input type="file" multiple className="hidden" onChange={event => setUploads(Array.from(event.target.files || []))} /></label><button type="button" className={quarkButton} onClick={() => folderInput.current?.click()}>选择文件夹</button><input ref={folderInput} type="file" multiple className="hidden" {...{ webkitdirectory: '', directory: '' }} onChange={event => setUploads(Array.from(event.target.files || []))} /></div>
        <p className="text-sm text-slate-500">本次最多 1,000 个文件、合计 1 GB。文件夹会保留目录结构，上传到当前网盘目录。</p>
        <div className="max-h-48 overflow-auto text-sm">{uploads.map((file, index) => <p key={index} className="break-all">{file.webkitRelativePath || file.name} · {(file.size / 1024).toFixed(1)} KB</p>)}</div>
      </>}
      {['move', 'batch-organize'].includes(tool) && !plan && <>
        <div className="flex items-center gap-3 text-sm"><span>目标：{target?.name || '请选择文件夹'}</span><button type="button" className="text-blue-600" onClick={() => setPicking(value => !value)}>选择目标目录</button></div>
        {picking && <QuarkFolderPicker onSelect={(fid, label) => { setTarget({ fid, name: label }); setPicking(false) }} />}
        {tool === 'batch-organize' && <><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={newFolder} onChange={event => setNewFolder(event.target.checked)} />在目标下创建新目录</label>{newFolder && <input className={quarkInput} maxLength={1000} placeholder="新目录的相对路径，例如：文档 / 课程" value={name} onChange={event => setName(event.target.value)} />}</>}
      </>}
      {['rename', 'batch-organize'].includes(tool) && <>
        <p className="text-sm text-slate-500">{plan ? '方案已经校验。请核对下面的改名和目标目录，再确认执行。' : '可逐个修改名称。先生成并校验方案，再确认执行。'}</p>
        {tool === 'batch-organize' && plan && <p className="break-all text-sm">目标目录：{target?.name}{newFolder ? ` / ${name}` : ''} · 涉及 {String(plan.preview?.summary?.affected_object_count ?? chosen.length)} 项</p>}
        <div className="max-h-72 overflow-auto rounded border border-slate-200 dark:border-slate-700"><table className="w-full text-left text-sm"><thead><tr><th className="p-2">原名称</th><th className="p-2">新名称</th></tr></thead><tbody>{chosen.map((file, index) => <tr key={file.fid}><td className="break-all p-2">{file.filename}</td><td className="p-2">{plan ? <span className="break-all">{newNames[index]}</span> : <input aria-label={`修改 ${file.filename} 名称`} className={quarkInput} maxLength={255} value={newNames[index]} onChange={event => setNewNames(values => values.map((value, row) => row === index ? event.target.value : value))} />}</td></tr>)}</tbody></table></div>
        {plan && <p className="text-xs text-slate-500">结果和撤回入口会保存在任务中心，官方撤回期限为 30 天。</p>}
      </>}
      {['summary', 'qa', 'organize'].includes(tool) && <><textarea autoFocus className={`${quarkInput} min-h-28`} maxLength={10000} value={query} onChange={event => setQuery(event.target.value)} placeholder={tool === 'organize' ? '例如：把今年的旅行照片按城市整理' : '输入总结要求或你想问的问题'} /><p className="text-sm text-slate-500">{tool === 'organize' ? '此功能由夸克按文字要求处理照片和视频，默认复制整理；大量文件需要在任务中心再次选择复制或移动。普通文档请使用「批量整理」。' : '由夸克对所选文件或文件夹执行；支持范围和会员权限以夸克返回结果为准。'}</p></>}
      <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={() => setTool(null)}>取消</button><button type="button" className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50" disabled={disabled} onClick={() => void submit()}>{['rename', 'batch-organize'].includes(tool) ? plan ? '确认执行方案' : '生成方案并校验' : tool === 'upload' ? '确认上传' : '确认提交'}</button></div>
    </QuarkDialog>}
  </div>
}
