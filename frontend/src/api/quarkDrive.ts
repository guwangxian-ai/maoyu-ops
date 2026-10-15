import request, { get, post } from '@/utils/request'
import { useAuthStore } from '@/store/authStore'
import { scopedLocalStorage } from '@/utils/scopedStorage'

export interface QuarkStatus {
  connected: boolean
  available: boolean
  nickname?: string
  vip_type?: string
  message?: string
  verified?: boolean
  account_id?: string
  used_bytes?: number
  capacity_bytes?: number
}

export interface QuarkApiResponse<T> {
  success: boolean
  message?: string
  data?: T
}

const PREFIX = '/api/v1/cloud-drive/quark'

// Share only unfinished reads. Settled responses are never retained, so the
// next refresh always reaches the server again.
const readsInFlight = new Map<string, Promise<unknown>>()
const readQuark = <T>(url: string, params?: Record<string, string>): Promise<T> => {
  const auth = useAuthStore.getState()
  const key = JSON.stringify([
    auth.user?.user_id ?? null, auth.isAuthenticated, auth.token,
    scopedLocalStorage.getItem('auth_token'), url,
    Object.entries(params || {}).sort(([left], [right]) => left.localeCompare(right)),
  ])
  const existing = readsInFlight.get(key)
  if (existing) return existing as Promise<T>
  const pending = get<T>(url, params ? { params } : undefined).finally(() => {
    if (readsInFlight.get(key) === pending) readsInFlight.delete(key)
  })
  readsInFlight.set(key, pending)
  return pending
}

export const getQuarkStatus = () => readQuark<QuarkApiResponse<QuarkStatus>>(`${PREFIX}/status`)

export const startQuarkAuthorization = () =>
  post<QuarkApiResponse<{ authorization_url: string }>>(`${PREFIX}/auth/start`)

export interface QuarkAuthSession {
  session_id: string
  status: 'starting' | 'waiting' | 'authorized' | 'expired' | 'failed' | 'cancelled'
  authorization_url: string
  expires_at: number
  message: string
}

export const startQuarkAuthSession = () => post<QuarkApiResponse<QuarkAuthSession>>(`${PREFIX}/auth/sessions`)
export const getQuarkAuthSession = (id: string) => get<QuarkApiResponse<QuarkAuthSession>>(`${PREFIX}/auth/sessions/${id}`)
export const cancelQuarkAuthSession = (id: string) => post<QuarkApiResponse<QuarkAuthSession>>(`${PREFIX}/auth/sessions/${id}/cancel`)

export const completeQuarkAuthorization = (code: string, sessionId?: string) =>
  post<QuarkApiResponse<{ connected: boolean }>>(`${PREFIX}/auth/complete`, { code, session_id: sessionId })

export interface QuarkFile {
  fid: string
  parent_fid?: string
  filename?: string
  size?: number
  file_type?: string
  category?: number
  updated_at?: number
  includeItems?: number
}

export interface QuarkFileList {
  files: QuarkFile[]
  total: number
  parent_fid: string
  keyword: string
}

export interface QuarkShare {
  id: string
  title: string
  fids: string[]
  share_url: string
  passcode: string
  url_type: 1 | 2
  expired_type: number
  created_at: string
}

export const getQuarkFiles = (parentFid: string, keyword = '', searchType = 'mix') =>
  readQuark<QuarkApiResponse<QuarkFileList>>(`${PREFIX}/files`, { parent_fid: parentFid, keyword, search_type: searchType })

export const getQuarkShares = () =>
  readQuark<QuarkApiResponse<{ shares: QuarkShare[]; total: number }>>(`${PREFIX}/shares`)

export const createQuarkShare = (payload: { fids: string[]; title: string; url_type: 1 | 2; expired_type: number }) =>
  post<QuarkApiResponse<{ share: QuarkShare; saved: boolean; message?: string }>>(`${PREFIX}/shares`, payload)

export interface QuarkJob {
  id: string
  operation: string
  title: string
  status: 'planned' | 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'needs_confirmation' | 'interrupted' | 'unknown' | 'submitted'
  message: string
  created_at: string
  updated_at: string
  progress?: { message?: string; completed_items?: number; data?: Record<string, unknown> }
  result?: { code?: number; msg?: string; data?: Record<string, unknown>; items?: Record<string, unknown>[] }
  preview?: { items?: RenamePlanItem[]; total?: number; summary?: Record<string, unknown>; proposal?: Record<string, unknown>; target_label?: string }
  files: { id: string; name: string; size: number }[]
}

export interface RenamePlanItem {
  fid: string
  parent_fid: string
  old_name: string
  new_name: string
}

export interface QuarkOperationPayload {
  fids?: string[]
  parent_fid?: string
  target_fid?: string
  name?: string
  query?: string
  keyword?: string
  url?: string
  passcode?: string
  page?: number
  size?: number
  search_type?: string
  record_id?: string
  task_id?: string
  state?: string
  confirmed?: boolean
  use_saved_directory?: boolean
}

export interface QuarkOperationResult {
  job?: QuarkJob
  message?: string
  data?: Record<string, unknown>
  items?: Record<string, unknown>[]
  files?: QuarkFile[]
  total?: number
}

export const runQuarkOperation = (operation: string, payload: QuarkOperationPayload = {}) =>
  post<QuarkApiResponse<QuarkOperationResult>>(`${PREFIX}/operations/${operation}`, payload)

export const getQuarkJobs = (offset = 0) =>
  get<QuarkApiResponse<{ jobs: QuarkJob[]; has_more: boolean }>>(`${PREFIX}/jobs`, { params: { offset, limit: 50 } })

export const getQuarkJob = (id: string) => get<QuarkApiResponse<{ job: QuarkJob }>>(`${PREFIX}/jobs/${id}`)

export const prepareQuarkRename = (items: RenamePlanItem[]) =>
  post<QuarkApiResponse<{ job: QuarkJob }>>(`${PREFIX}/rename/plans`, { items })

export const prepareQuarkOrganize = (proposal: Record<string, unknown>, targetLabel?: string) =>
  post<QuarkApiResponse<{ job: QuarkJob }>>(`${PREFIX}/batch-organize/plans`, { proposal, target_label: targetLabel })

export const executeQuarkPlan = (id: string) =>
  post<QuarkApiResponse<{ job: QuarkJob }>>(`${PREFIX}/jobs/${id}/execute`, { confirmed: true })

export const followupQuarkJob = (id: string, action: 'revert' | 'rename-resume' | 'resume' | 'undo' | 'undo-resume') =>
  post<QuarkApiResponse<{ job: QuarkJob }>>(`${PREFIX}/jobs/${id}/followup`, { action, confirmed: true })

export const cancelQuarkJob = (id: string) =>
  post<QuarkApiResponse<{ id: string; message: string }>>(`${PREFIX}/jobs/${id}/cancel`, { confirmed: true })

export const revokeQuarkAuthorization = () =>
  post<QuarkApiResponse<{ authorization_url: string; message: string }>>(`${PREFIX}/auth/revoke`, { confirmed: true })

export const uploadQuarkFiles = (files: File[], parentFid: string, recordId?: string) => {
  const form = new FormData()
  files.forEach(file => form.append('files', file))
  form.append('relative_paths', JSON.stringify(files.map(file => file.webkitRelativePath || file.name)))
  form.append('parent_fid', parentFid)
  if (recordId) form.append('record_id', recordId)
  return post<QuarkApiResponse<{ job: QuarkJob }>>(`${PREFIX}/upload`, form, { timeout: 30 * 60 * 1000, headers: { 'Content-Type': undefined } })
}

export const downloadQuarkJobFile = async (jobId: string, fileId: string) => {
  // Renew JSON-based authentication before starting a binary response, whose
  // error envelope cannot be inspected by the shared JSON interceptor.
  const authorized = await getQuarkJob(jobId)
  if (!authorized.success) throw new Error(authorized.message || '无法访问下载任务')
  const response = await request.get<Blob>(`${PREFIX}/jobs/${jobId}/files/${fileId}`, { responseType: 'blob' })
  if (!response.headers['content-disposition']) {
    let message = '获取下载文件失败'
    try { const error = JSON.parse(await response.data.text()); message = error.message || error.detail || message } catch { /* Preserve fallback for non-JSON errors. */ }
    throw new Error(message)
  }
  return response.data
}
