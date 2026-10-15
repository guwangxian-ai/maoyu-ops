import { useCallback, useEffect, useRef, useState } from 'react'
import { FileText, Loader2, Search } from 'lucide-react'
import { getItemsPaginated } from '@/api/items'
import {
  analyzeCloudAsset, createCloudAssetCard, getCloudAsset, renameCloudAsset, retryCloudAsset,
  shareCloudAsset, syncCloudAsset, type CloudAsset, type CloudAssetAvailability,
} from '@/api/cloudAssets'
import { getApiErrorMessage } from '@/utils/request'
import { QuarkDialog, quarkButton, quarkInput } from './QuarkTools'

export type CloudAssetAction = 'sync' | 'shares' | 'card' | 'rename' | 'analyze' | 'retry'
const actionTitles: Record<CloudAssetAction, string> = { sync: '确认同步资料', shares: '创建多网盘分享', card: '创建关联发货卡券', rename: '修改资料名称', analyze: '分析资料内容', retry: '恢复失败任务' }
const providerNames = { quark: '夸克网盘', baidu: '百度网盘' }
const periods = [[1, '1 天'], [7, '7 天'], [30, '30 天'], [0, '永久有效']] as const
interface ItemChoice { id: string; title: string }

function AssetCardItems({ selected, onChange }: { selected: ItemChoice[]; onChange: (items: ItemChoice[]) => void }) {
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<ItemChoice[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const version = useRef(0)
  const load = useCallback(async () => {
    const sequence = ++version.current
    setLoading(true)
    setError('')
    setItems([])
    try {
      const result = await getItemsPaginated(page, 20, undefined, { keyword: keyword || null })
      if (sequence !== version.current) return
      if (!result.success) throw new Error('读取可关联商品失败')
      const unique = new Map(result.data.map(item => [item.item_id, { id: item.item_id, title: item.title || item.item_title || item.item_id }]))
      setItems([...unique.values()])
      setTotal(result.total)
    } catch (reason) { if (sequence === version.current) setError(getApiErrorMessage(reason, '读取可关联商品失败')) }
    finally { if (sequence === version.current) setLoading(false) }
  }, [page, keyword])
  const invalidate = useCallback(() => { version.current++ }, [])
  useEffect(() => { void load(); return invalidate }, [load, invalidate])

  return <div className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <p className="text-sm font-medium">关联商品（可选）</p>
    <div className="flex gap-2"><input className={quarkInput} aria-label="查找要关联的商品" value={search} onChange={event => setSearch(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); setKeyword(search.trim()); setPage(1); if (keyword === search.trim() && page === 1) void load() } }} placeholder="搜索商品名称或 ID" maxLength={100} /><button type="button" className={quarkButton} onClick={() => { setKeyword(search.trim()); setPage(1); if (keyword === search.trim() && page === 1) void load() }}><Search className="h-4 w-4" />查询</button></div>
    {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    <div className="max-h-48 space-y-1 overflow-auto">{loading ? <p className="flex justify-center gap-2 py-5 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />读取商品…</p> : items.length ? items.map(item => <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded px-1 py-2 text-sm hover:bg-slate-50 dark:hover:bg-slate-900"><input className="mt-1" type="checkbox" checked={selected.some(value => value.id === item.id)} disabled={selected.length >= 100 && !selected.some(value => value.id === item.id)} onChange={event => onChange(event.target.checked ? [...selected, item] : selected.filter(value => value.id !== item.id))} /><span className="break-all">{item.title}<span className="mt-1 block text-xs text-slate-400">{item.id}</span></span></label>) : !error && <p className="py-5 text-center text-sm text-slate-500">没有匹配商品，也可以先创建卡券后关联。</p>}</div>
    <div className="flex items-center justify-between gap-2 text-xs text-slate-500"><span>已选择 {selected.length} 个商品</span><div className="flex items-center gap-2"><button type="button" className={quarkButton} disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span>{page}</span><button type="button" className={quarkButton} disabled={loading || page * 20 >= total} onClick={() => setPage(value => value + 1)}>下一页</button></div></div>
    {selected.length > 0 && <div className="max-h-28 space-y-1 overflow-auto">{selected.map(item => <button key={item.id} type="button" className="block max-w-full truncate text-left text-xs text-blue-600" title={`取消关联 ${item.title}`} onClick={() => onChange(selected.filter(value => value.id !== item.id))}>× {item.title}</button>)}</div>}
  </div>
}

export function CloudAssetActionDialog({ action, asset, availability, onUpdated, onClose }: {
  action: CloudAssetAction
  asset: CloudAsset
  availability: CloudAssetAvailability | null
  onUpdated: (asset: CloudAsset, message?: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState(asset.name)
  const [period, setPeriod] = useState<0 | 1 | 7 | 30>(7)
  const [items, setItems] = useState<ItemChoice[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async () => {
    if (busy) return
    if (action === 'rename' && !name.trim()) { setError('资料名称不能为空。'); return }
    if (action === 'card' && items.length > 100) { setError('每次最多关联 100 个商品。'); return }
    setBusy(true)
    setError('')
    try {
      const result = action === 'sync' ? await syncCloudAsset(asset.id)
        : action === 'shares' ? await shareCloudAsset(asset.id, period)
        : action === 'card' ? await createCloudAssetCard(asset.id, items.map(item => item.id))
        : action === 'rename' ? await renameCloudAsset(asset.id, name.trim())
        : action === 'analyze' ? await analyzeCloudAsset(asset.id)
        : await retryCloudAsset(asset.id)
      if (result.data?.id === asset.id) onUpdated(result.data, result.message)
      if (!result.success || result.data?.id !== asset.id) throw new Error(result.message || '操作未完成，请查看各网盘状态')
      onClose()
    } catch (reason) {
      setError(`${getApiErrorMessage(reason, '操作未完成')}。请先检查资源状态，避免重复提交。`)
      try { const current = await getCloudAsset(asset.id); if (current.data?.id === asset.id) onUpdated(current.data) }
      catch { /* A failed follow-up must not hide the original operation error. */ }
    } finally { setBusy(false) }
  }

  return <QuarkDialog title={action === 'card' && asset.card_id ? '更新关联发货卡券' : actionTitles[action]} compact={action !== 'card'} busy={busy} error={error} onClose={onClose}>
    <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit() }}>
      <div className="flex items-start gap-3 rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-900"><FileText className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" /><div className="min-w-0"><p className="break-all font-medium">{asset.name}</p><p className="mt-1 text-xs text-slate-500">版本 {asset.version} · {asset.files.length} 个文件</p></div></div>
      {action === 'sync' && <><p className="text-sm leading-7 text-slate-600 dark:text-slate-300">确认将这份资料同步到指定网盘。每个平台的上传与失败原因会独立显示，任务进度保存在服务端。</p><ul className="space-y-2 text-sm">{asset.targets.map(target => <li key={target.provider} className="flex justify-between gap-3"><span>{providerNames[target.provider]}</span><span className="text-slate-500">{availability?.providers.find(item => item.provider === target.provider)?.connected ? '已保存授权' : '请先授权'}</span></li>)}</ul></>}
      {action === 'shares' && <><p className="text-sm leading-7 text-slate-600 dark:text-slate-300">在全部指定网盘创建资料分享。只有各平台分享都成功且有效，资料才能用于创建发货卡券。</p><label className="block space-y-1 text-sm"><span>分享有效期</span><select className={quarkInput} value={period} onChange={event => setPeriod(Number(event.target.value) as typeof period)}>{periods.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{period === 0 && <p className="text-xs leading-6 text-slate-500">永久链接不会自动失效，请核对资料范围。</p>}{asset.targets.map(target => { const status = availability?.providers.find(item => item.provider === target.provider); return <p key={target.provider} className="text-xs leading-6 text-amber-700 dark:text-amber-400">{providerNames[target.provider]}：{status?.share_notice || (target.provider === 'baidu' ? '是否可分享取决于百度服务权限，可能需要开通付费服务。' : '分享权限以平台实际返回结果为准。')}</p> })}</>}
      {action === 'card' && <><p className="text-sm leading-7 text-slate-600 dark:text-slate-300">将已发布版本 {asset.published_version} 的多网盘发货文本{asset.card_id ? '更新到已有卡券' : '保存为关联卡券'}。服务端会再次核验全部已发布分享的有效期。</p><textarea readOnly aria-label="卡券发货内容" className={`${quarkInput} min-h-32 resize-y`} value={asset.delivery_content} />{!asset.card_id && <AssetCardItems selected={items} onChange={setItems} />}{!asset.card_id && <p className="text-xs leading-6 text-slate-500">不选择商品时先创建卡券，可在卡券管理中继续关联。</p>}</>}
      {action === 'rename' && <><label className="block space-y-1 text-sm"><span>统一资料名称</span><input autoFocus required className={quarkInput} maxLength={100} value={name} onChange={event => setName(event.target.value)} /></label><p className="text-xs leading-6 text-slate-500">此操作修改资源显示名称，云端文件名保持原样。</p></>}
      {action === 'analyze' && <p className="text-sm leading-7 text-slate-600 dark:text-slate-300">提取支持的文件文本和元数据，在资料详情中显示实际结果。无法解析的格式会返回原因。</p>}
      {action === 'retry' && <p className="text-sm leading-7 text-slate-600 dark:text-slate-300">确认恢复失败或中断的任务。已完成结果会保留，服务端会核对各平台进度；结果未知的平台请先确认云端文件，避免重复操作。</p>}
      <div className="flex justify-end gap-2"><button type="button" className={quarkButton} onClick={onClose}>取消</button><button type="submit" disabled={action === 'rename' && !name.trim()} className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">{action === 'rename' ? '确认修改名称' : action === 'card' ? asset.card_id ? '确认更新关联卡券' : '确认创建卡券' : action === 'shares' ? '确认创建分享' : action === 'analyze' ? '确认提取与分析' : action === 'retry' ? '确认恢复任务' : '确认开始同步'}</button></div>
    </form>
  </QuarkDialog>
}
