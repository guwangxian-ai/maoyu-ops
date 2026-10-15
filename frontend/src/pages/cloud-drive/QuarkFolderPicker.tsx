import { useCallback, useEffect, useRef, useState } from 'react'
import { Folder, Loader2 } from 'lucide-react'
import { getQuarkFiles, type QuarkFile } from '@/api/quarkDrive'

export function QuarkFolderPicker({ onSelect }: { onSelect: (fid: string, name: string) => void }) {
  const [path, setPath] = useState([{ fid: '0', name: '全部文件' }])
  const [folders, setFolders] = useState<QuarkFile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const sequence = useRef(0)
  const invalidate = useCallback(() => { sequence.current++ }, [])
  const current = path[path.length - 1]
  useEffect(() => {
    const id = ++sequence.current
    setLoading(true); setError('')
    getQuarkFiles(current.fid).then(result => {
      if (id !== sequence.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取文件夹失败')
      setFolders(result.data.files.filter(file => file.file_type === '0' || file.category === 0))
    }).catch(reason => { if (id === sequence.current) setError(reason instanceof Error ? reason.message : '读取文件夹失败') })
      .finally(() => { if (id === sequence.current) setLoading(false) })
    return invalidate
  }, [current.fid, invalidate])
  return <div className="space-y-2 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span className="break-all text-slate-500">{path.map(item => item.name).join(' / ')}</span><div className="flex gap-3">
      {path.length > 1 && <button type="button" className="text-blue-600" onClick={() => setPath(items => items.slice(0, -1))}>上一级</button>}
      <button type="button" disabled={loading || !!error} className="font-medium text-blue-600 disabled:opacity-50" onClick={() => onSelect(current.fid, path.map(item => item.name).join(' / '))}>选择此目录</button>
    </div></div>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {loading ? <Loader2 className="h-4 w-4 animate-spin text-blue-500" /> : <div className="max-h-44 space-y-1 overflow-y-auto">
      {folders.map(folder => <button key={folder.fid} type="button" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-700" onClick={() => setPath(items => [...items, { fid: folder.fid, name: folder.filename || '文件夹' }])}><Folder className="h-4 w-4 shrink-0 text-amber-500" /><span className="break-all">{folder.filename || '未命名文件夹'}</span></button>)}
      {folders.length === 0 && <p className="text-xs text-slate-400">没有下级文件夹</p>}
    </div>}
  </div>
}
