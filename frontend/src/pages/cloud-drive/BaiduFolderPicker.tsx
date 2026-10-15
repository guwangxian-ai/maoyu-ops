import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronRight, Folder, Loader2, RefreshCw } from 'lucide-react'
import { getBaiduFiles, type BaiduFile } from '@/api/baiduDrive'
import { getApiErrorMessage } from '@/utils/request'
import { quarkButton } from './QuarkTools'

const rootPath = '/'

export function BaiduFolderPicker({ initialDir, onSelect }: { initialDir: string; onSelect: (dir: string) => void }) {
  const [dir, setDir] = useState(initialDir === '/' ? rootPath : initialDir)
  const [page, setPage] = useState(1)
  const [folders, setFolders] = useState<BaiduFile[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const version = useRef(0)
  const load = useCallback(async () => {
    const current = ++version.current
    setLoading(true)
    setError('')
    setFolders([])
    try {
      const result = await getBaiduFiles({ dir, category: 'folder', page, page_size: 100 })
      if (current !== version.current) return
      if (!result.success || !result.data) throw new Error(result.message || '读取目录失败')
      setFolders(result.data.files.filter(file => file.isdir))
      setHasMore(result.data.has_more)
    } catch (reason) {
      if (current === version.current) setError(getApiErrorMessage(reason, '读取目录失败'))
    } finally { if (current === version.current) setLoading(false) }
  }, [dir, page])
  const invalidate = useCallback(() => { version.current++ }, [])
  useEffect(() => { void load(); return invalidate }, [load, invalidate])
  const enter = (next: string) => { setDir(next === '/' ? rootPath : next); setPage(1) }
  const parts = dir.split('/').filter(Boolean)

  return <div className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <nav aria-label="选择目标目录" className="flex flex-wrap items-center gap-1 text-sm"><button type="button" className="text-blue-600" onClick={() => enter(rootPath)}>全部文件</button>{parts.map((part, index) => <span key={index} className="inline-flex items-center gap-1"><ChevronRight className="h-3.5 w-3.5 text-slate-400" /><button type="button" className="max-w-40 truncate text-blue-600" onClick={() => enter(`/${parts.slice(0, index + 1).join('/')}`)} title={part}>{part}</button></span>)}</nav>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="max-h-52 space-y-1 overflow-y-auto">{loading ? <p className="flex items-center justify-center gap-2 py-6 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />读取目录…</p> : folders.length ? folders.map(folder => <button type="button" key={folder.fs_id} className="flex w-full items-center gap-2 rounded px-2 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800" onClick={() => enter(folder.path)}><Folder className="h-4 w-4 shrink-0 text-amber-500" /><span className="break-all">{folder.filename}</span><ChevronRight className="ml-auto h-4 w-4 shrink-0 text-slate-400" /></button>) : !error && <p className="py-6 text-center text-sm text-slate-500">此页没有子文件夹。</p>}</div>
    <div className="flex flex-wrap items-center justify-between gap-2"><button type="button" className={quarkButton} onClick={() => void load()} disabled={loading}><RefreshCw className="h-3.5 w-3.5" />刷新</button><div className="flex items-center gap-2"><button type="button" className={quarkButton} disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span className="text-xs text-slate-500">第 {page} 页</span><button type="button" className={quarkButton} disabled={loading || !hasMore} onClick={() => setPage(value => value + 1)}>下一页</button></div><button type="button" className="rounded-md bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700" onClick={() => onSelect(dir)}>使用当前目录</button></div>
  </div>
}
