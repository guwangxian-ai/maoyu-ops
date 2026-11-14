import { useEffect, useState } from 'react'
import { getMarketHistory, marketTime, type MarketPage, type MarketObservation } from '@/api/marketResearch'
import { getApiErrorMessage } from '@/utils/apiError'

export function MarketHistory({ itemId, scope }: { itemId: string; scope?: string }) {
  const [page, setPage] = useState(1)
  const [data, setData] = useState<MarketPage<MarketObservation> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => { setPage(1) }, [itemId, scope])
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    getMarketHistory(itemId, scope, page).then(result => {
      if (!active) return
      if (!result.success || !result.data) throw new Error(result.message || '历史加载失败')
      setData(result.data)
    }).catch(err => { if (active) setError(getApiErrorMessage(err, '历史加载失败')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [itemId, scope, page])
  return <section aria-label="商品观测历史" className="space-y-3">
    <h3 className="font-medium">观测历史</h3>
    {loading ? <p role="status">正在加载历史…</p> : error ? <p role="alert" className="text-red-600">{error}</p> :
      !data?.list.length ? <p className="text-slate-500">暂无历史。启用此功能后的有效采样才会记录。</p> : <>
        <p className="text-sm text-slate-500">北京时间；未知保留为空。不同范围与来源不混算增长。</p>
        <div className="overflow-x-auto"><table className="w-full text-sm text-left">
          <thead><tr>{['采样时间', '挂牌价', '想要数', '浏览量', '人数来源'].map(t => <th key={t} className="p-2 whitespace-nowrap">{t}</th>)}</tr></thead>
          <tbody>{data.list.map(row => <tr key={row.id} className="border-t border-slate-200 dark:border-slate-700">
            <td className="p-2 whitespace-nowrap">{marketTime(row.observed_at)}</td>
            <td className="p-2">{row.price == null ? '未知' : `¥${row.price}`}</td>
            <td className="p-2">{row.want_approximate ? '约 ' : ''}{row.want_count ?? '未知'}</td>
            <td className="p-2">{row.browse_count ?? '未知'}</td>
            <td className="p-2">{row.want_source?.startsWith('detail') ? '详情' : row.want_source ? '搜索标签' : '未提供'}</td>
          </tr>)}</tbody>
        </table></div>
        <div className="flex items-center gap-3 text-sm">
          <button className="btn-ios-secondary" disabled={page === 1} onClick={() => setPage(p => p - 1)}>上一页</button>
          <span>第 {page} 页，共 {data.total} 条</span>
          <button className="btn-ios-secondary" disabled={page * data.page_size >= data.total} onClick={() => setPage(p => p + 1)}>下一页</button>
        </div>
      </>}
  </section>
}
