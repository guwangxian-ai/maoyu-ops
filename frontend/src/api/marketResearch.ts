import { get, post } from '@/utils/request'
import type { ApiResponse } from '@/types'

const PREFIX = '/api/v1/market-research'
export interface MarketRun {
  id: string; sample_key: string; scope_key: string; keyword: string; status: string
  source: string; monitor_task_id: number | null; started_at: string; finished_at: string | null
  item_count: number; pages_completed: number; pages_requested: number; message: string | null
}
export interface MarketObservation {
  id: number; run_id: string; scope_key: string; item_id: string; title: string; item_url: string
  price: number | null; want_count: number | null; browse_count: number | null; want_approximate: boolean
  price_source: string | null; want_source: string | null; browse_source: string | null; observed_at: string
}
export interface MetricChange { value: number | null; reason: string | null }
export interface MarketCandidate extends MarketObservation {
  sample_count: number; changes: Record<'price' | 'want_count' | 'browse_count', MetricChange>
  candidate_signal: string; first_observed_at: string
}
export interface MarketCandidates {
  list: MarketCandidate[]; run: MarketRun | null; latest_attempt: MarketRun | null
  summary: { sample_items: number; known_want_items?: number; price_samples?: number; median_asking_price?: number | null }
  notice?: string
}
export interface MarketMonitorState {
  task_id: number; keyword: string; status: string; is_enabled: boolean; consecutive_failures: number
  paused_until: string | null; last_checked_at: string | null; last_success_at: string | null; message: string | null
}
export interface MarketPage<T> { list: T[]; total: number; page: number; page_size: number }
export interface MarketSampleInput {
  keyword: string; account_id: string; sample_key: string; detail_limit: number
  pages: number; page_size: number; start_page: number; price_min: number | null; price_max: number | null
}
export const listMarketSamples = (page = 1) => get<ApiResponse<MarketPage<MarketRun>>>(`${PREFIX}/samples?page=${page}`)
export const getMarketCandidates = (scope: string, include = '', exclude = '') =>
  get<ApiResponse<MarketCandidates>>(`${PREFIX}/candidates?${new URLSearchParams({ scope_key: scope, include_terms: include, exclude_terms: exclude })}`)
export const getMarketHistory = (itemId: string, scope?: string, page = 1) =>
  get<ApiResponse<MarketPage<MarketObservation>>>(`${PREFIX}/history/${encodeURIComponent(itemId)}?${new URLSearchParams({ page: String(page), ...(scope ? { scope_key: scope } : {}) })}`)
export const listMarketMonitorStates = () => get<ApiResponse<MarketPage<MarketMonitorState>>>(`${PREFIX}/monitor-states?page_size=100`)
export const collectMarketSample = (body: MarketSampleInput) =>
  post<ApiResponse<{ run: MarketRun; replayed: boolean }>>(`${PREFIX}/samples`, body, { timeout: 110000 })

export const marketStatus: Record<string, string> = {
  success: '完整采样', partial: '部分采样', failed: '采样失败', running: '采样中', interrupted: '已中断',
  paused: '冷却暂停', ready: '正常', degraded: '采集异常',
}
export const metricReason: Record<string, string> = {
  need_two_samples: '待第二次采样', missing_value: '数据不足', source_changed: '来源不同',
  rounded_counter: '近似人数', counter_decreased: '人数回落',
}
export const marketTime = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'
