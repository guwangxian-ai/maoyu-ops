import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAccountDetails } from '@/api/accounts'
import { collectMarketSample, getMarketCandidates, listMarketSamples, listMarketMonitorStates,
  marketStatus, marketTime, metricReason, type MarketCandidates, type MarketRun, type MarketMonitorState,
  type MarketSampleInput, type MetricChange } from '@/api/marketResearch'
import { getApiErrorMessage } from '@/utils/apiError'
import { scopedSessionStorage } from '@/utils/scopedStorage'
import { useAuthStore } from '@/store/authStore'
import { MarketHistory } from './MarketHistory'

const changeText = (change: MetricChange) => change.value == null ? metricReason[change.reason || ''] || '不可比' : `${change.value > 0 ? '+' : ''}${change.value}`

export function MarketResearch() {
  const [params, setParams] = useSearchParams()
  const scope = params.get('scope') || ''
  const page = Math.max(1, Math.min(1000, Number(params.get('page')) || 1))
  const userId = useAuthStore(s => s.user?.user_id)
  const pendingKey = `market-sample-pending:${userId}`
  const [accounts, setAccounts] = useState<{ id: string; note?: string }[]>([])
  const [accountId, setAccountId] = useState('')
  const [keyword, setKeyword] = useState('')
  const [detail, setDetail] = useState(false)
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')
  const [runs, setRuns] = useState<MarketRun[]>([])
  const [total, setTotal] = useState(0)
  const [states, setStates] = useState<MarketMonitorState[]>([])
  const [data, setData] = useState<MarketCandidates | null>(null)
  const [historyId, setHistoryId] = useState('')
  const [loading, setLoading] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [sampling, setSampling] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState<MarketSampleInput | null>(() => {
    try { return JSON.parse(scopedSessionStorage.getItem(pendingKey) || 'null') } catch { return null }
  })
  const sequence = useRef(0)
  const selectScope = (value: string) => {
    setParams(prev => { const next = new URLSearchParams(prev); next.set('scope', value); return next })
    setHistoryId('')
  }
  const listSequence = useRef(0)
  const load = useCallback(async () => {
    const seq = ++listSequence.current
    setLoading(true); setError('')
    try {
      const [samples, status] = await Promise.all([listMarketSamples(page), listMarketMonitorStates()])
      if (!samples.success || !samples.data) throw new Error(samples.message || '批次加载失败')
      if (!status.success || !status.data) throw new Error(status.message || '监控状态加载失败')
      if (seq === listSequence.current) { setRuns(samples.data.list); setTotal(samples.data.total); setStates(status.data.list) }
    } catch (err) { if (seq === listSequence.current) setError(getApiErrorMessage(err, '加载失败')) }
    finally { if (seq === listSequence.current) setLoading(false) }
  }, [page])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    let active = true
    getAccountDetails().then(rows => {
      if (active) { const enabled = rows.filter(a => a.enabled); setAccounts(enabled); setAccountId(enabled[0]?.id || '') }
    }).catch(err => { if (active) setError(getApiErrorMessage(err, '账号加载失败')) })
    return () => { active = false }
  }, [])
  const analyze = useCallback(async () => {
    const seq = ++sequence.current
    if (!scope) { setData(null); setAnalyzing(false); return }
    setAnalyzing(true); setError(''); setData(null)
    try {
      const result = await getMarketCandidates(scope, include, exclude)
      if (!result.success || !result.data) throw new Error(result.message || '候选加载失败')
      if (seq === sequence.current) setData(result.data)
    } catch (err) { if (seq === sequence.current) setError(getApiErrorMessage(err, '候选加载失败')) }
    finally { if (seq === sequence.current) setAnalyzing(false) }
  }, [scope, include, exclude])
  // Filters are applied explicitly; no new network call on every keystroke.
  const analyzeRef = useRef(analyze); analyzeRef.current = analyze
  useEffect(() => { void analyzeRef.current(); return () => { sequence.current++ } }, [scope])
  const sample = async (event: React.FormEvent) => {
    event.preventDefault()
    if (sampling) return
    const input = pending || { keyword: keyword.trim(), account_id: accountId, sample_key: crypto.randomUUID(),
      detail_limit: detail ? 5 : 0, pages: 1, page_size: 20, start_page: 1, price_min: null, price_max: null }
    if (!input.keyword || !input.account_id) return
    setSampling(true); setError(''); setNotice('')
    scopedSessionStorage.setItem(pendingKey, JSON.stringify(input)); setPending(input)
    try {
      const result = await collectMarketSample(input)
      if (result.data?.run) {
        const run = result.data.run
        setNotice(`${marketStatus[run.status] || run.status}：${run.item_count} 件。${run.message || ''}`)
        if (!['running'].includes(run.status)) {
          setKeyword(input.keyword); setAccountId(input.account_id); setDetail(input.detail_limit > 0)
          setPending(null); scopedSessionStorage.removeItem(pendingKey)
        }
        selectScope(run.scope_key)
      } else {
        // A definitive validation rejection made no sampling run; let the user correct the form.
        if (result.code !== 409) { setPending(null); scopedSessionStorage.removeItem(pendingKey) }
        throw new Error(result.message || '采样未完成')
      }
      await load()
      await analyzeRef.current()
    } catch (err) { setError(`${getApiErrorMessage(err, '采样中断')}。重试会沿用本轮编号，也可刷新批次查看结果。`) }
    finally { setSampling(false) }
  }

  return <div className="space-y-5">
    <div className="page-header flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="page-title">竞品研究</h1><p className="page-description">保存采样，对比价格与关注变化，供选品判断。</p></div>
      <button className="btn-ios-secondary" disabled={loading} onClick={() => { void load(); void analyze() }}>刷新记录</button>
    </div>
    {error && <p role="alert" className="text-red-600 break-words">{error}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {states.filter(s => s.status !== 'ready').map(s => <div key={s.task_id} role="status" className="p-3 rounded border border-amber-300 bg-amber-50 text-amber-900">
      <strong>{s.keyword} · {marketStatus[s.status]}</strong><p>{s.message}。连续失败 {s.consecutive_failures} 次；最近检查 {marketTime(s.last_checked_at)}。</p>
      <p>{s.is_enabled ? `冷却至 ${marketTime(s.paused_until)} 后按原调度试采，成功才恢复。` : '任务已停用，保持停用。'}</p>
    </div>)}
    <form onSubmit={sample} className="vben-card p-4 flex flex-wrap gap-3 items-end">
      <label className="flex flex-col gap-1 text-sm flex-1 min-w-[180px]">搜索关键词
        <input className="input-ios" required={!pending} maxLength={200} value={pending?.keyword ?? keyword} disabled={sampling || !!pending} onChange={e => setKeyword(e.target.value)} placeholder="例如：小店进销存表格" /></label>
      <label className="flex flex-col gap-1 text-sm">采集账号
        <select className="input-ios" required={!pending} value={pending?.account_id ?? accountId} disabled={sampling || !!pending} onChange={e => setAccountId(e.target.value)}>
          <option value="">选择已启用账号</option>{accounts.map(a => <option value={a.id} key={a.id}>{a.note || a.id}</option>)}
        </select></label>
      <label className="flex items-center gap-2 text-sm py-2"><input type="checkbox" checked={pending ? pending.detail_limit > 0 : detail} disabled={sampling || !!pending} onChange={e => setDetail(e.target.checked)} />补充前 5 件详情</label>
      <button className="btn-ios-primary" disabled={sampling || (!pending && (!keyword.trim() || !accountId))}>{sampling ? '采样中…' : pending ? '查看本轮结果' : '采样一页'}</button>
      <p className="text-xs text-slate-500 w-full">仅查询个人卖家公开商品并保存本人的研究记录。默认一页 20 件；不创建持续任务。历史保留 90 天。</p>
    </form>
    <section className="space-y-3" aria-label="采样批次">
      <h2 className="text-base font-semibold">采样批次</h2>
      {loading ? <p role="status">正在加载批次…</p> : !runs.length ? <p className="text-slate-500">暂无采样记录。采样一次可建立基线，之后按相同范围复查。</p> : <>
        <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr>{['关键词', '最近检查', '结果', '样本', ''].map((t, i) => <th className="p-2 whitespace-nowrap" key={i}>{t}</th>)}</tr></thead>
          <tbody>{runs.map(run => <tr key={run.id} className="border-t border-slate-200 dark:border-slate-700">
            <td className="p-2">{run.keyword}</td><td className="p-2 whitespace-nowrap">{marketTime(run.started_at)}</td><td className="p-2">{marketStatus[run.status] || run.status}</td><td className="p-2">{run.item_count}</td>
            <td className="p-2"><button className="btn-ios-secondary" onClick={() => selectScope(run.scope_key)} aria-pressed={scope === run.scope_key}>查看候选</button></td>
          </tr>)}</tbody></table></div>
        <div className="flex items-center gap-3 text-sm"><button className="btn-ios-secondary" disabled={page === 1} onClick={() => setParams(prev => { const next = new URLSearchParams(prev); next.set('page', String(page - 1)); return next })}>上一页</button><span>第 {page} 页，共 {total} 批</span><button className="btn-ios-secondary" disabled={page * 20 >= total} onClick={() => setParams(prev => { const next = new URLSearchParams(prev); next.set('page', String(page + 1)); return next })}>下一页</button></div>
      </>}
    </section>
    {scope && <section className="space-y-3" aria-label="候选证据">
      <h2 className="text-base font-semibold">候选证据 · 最近 30 天</h2>
      <form onSubmit={e => { e.preventDefault(); void analyze() }} className="flex flex-wrap gap-3 items-end">
        <label className="text-sm flex flex-col gap-1">包含任一词<input className="input-ios" value={include} maxLength={300} onChange={e => setInclude(e.target.value)} placeholder="逗号分隔" /></label>
        <label className="text-sm flex flex-col gap-1">排除词<input className="input-ios" value={exclude} maxLength={300} onChange={e => setExclude(e.target.value)} placeholder="逗号分隔" /></label>
        <button className="btn-ios-secondary" disabled={analyzing}>应用筛选</button>
      </form>
      {analyzing ? <p role="status">正在比较样本…</p> : data && <>
        {data.latest_attempt?.status !== 'success' && <p className="text-amber-700">最近一轮：{marketStatus[data.latest_attempt?.status || ''] || '无有效记录'}。{data.latest_attempt?.message}</p>}
        <p className="text-sm text-slate-500">样本 {data.summary.sample_items} 件，人数已知 {data.summary.known_want_items ?? 0} 件；挂牌价中位数 {data.summary.median_asking_price == null ? '未知' : `¥${data.summary.median_asking_price}`}。关注变化不等于销量。</p>
        {!data.list.length ? <p>该范围暂无有效样本。</p> : <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr>{['商品', '挂牌价', '想要数', '想要净变化', '记录数', ''].map((t, i) => <th className="p-2 whitespace-nowrap" key={i}>{t}</th>)}</tr></thead>
          <tbody>{data.list.map(item => <tr key={item.id} className={`border-t border-slate-200 dark:border-slate-700 ${item.candidate_signal === 'filtered_out' ? 'text-slate-500' : ''}`}>
            <td className="p-2 min-w-[180px] max-w-md break-words">{item.title}{item.candidate_signal === 'filtered_out' && <span className="block text-xs">不符合关键词筛选</span>}</td><td className="p-2">{item.price == null ? '未知' : `¥${item.price}`}</td><td className="p-2">{item.want_approximate ? '约 ' : ''}{item.want_count ?? '未知'}</td><td className="p-2">{changeText(item.changes.want_count)}</td><td className="p-2">{item.sample_count}</td>
            <td className="p-2"><button className="btn-ios-secondary" onClick={() => setHistoryId(item.item_id)}>看历史</button></td>
          </tr>)}</tbody></table></div>}
      </>}
      {historyId && <div className="vben-card p-4"><MarketHistory key={`${scope}:${historyId}`} itemId={historyId} scope={scope} /></div>}
    </section>}
  </div>
}
