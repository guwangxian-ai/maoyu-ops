import request, { get, post } from '@/utils/request'

const PREFIX = '/api/v1/cloud-drive/baidu'

export interface BaiduApiResponse<T> {
  success: boolean
  message?: string
  data?: T
}

export type BaiduAuthMode = 'baidupcs-go'
export type BaiduCategory = 'all' | 'video' | 'audio' | 'image' | 'doc' | 'app' | 'other' | 'folder'

export interface BaiduStatus {
  connected: boolean
  available: boolean
  verified?: boolean
  account_id?: string
  nickname?: string
  vip_type?: number
  used_bytes?: number
  capacity_bytes?: number
  expires_at?: number
  auth_mode: BaiduAuthMode
  message?: string
}

export interface BaiduAuthConfig {
  mode: BaiduAuthMode
  source: 'qjfoidnh/BaiduPCS-Go'
  root_path: '/'
  supports_qrcode: boolean
}

export interface BaiduAuthStart {
  qrcode_image: string
  session_id: string
  mode: BaiduAuthMode
  message: string
  expires_at: number
}

export interface BaiduAuthSession {
  session_id: string
  status: 'starting' | 'waiting' | 'scanned' | 'authorizing' | 'authorized' | 'expired' | 'failed' | 'cancelled'
  expires_at: number
  message: string
}

export interface BaiduFile {
  fs_id: string
  path: string
  filename: string
  size: number
  isdir: boolean
  category: number
  updated_at?: number
  thumbnail?: string
  md5?: string
  abstract?: string
  content?: string
  return_url?: string
  return_markdown?: string
}

export interface BaiduFileList {
  files: BaiduFile[]
  dir: string
  keyword: string
  page: number
  page_size: number
  has_more: boolean
  total: number
}

export interface BaiduShare {
  id: string
  title: string
  fsids: string[]
  paths?: string[]
  share_url: string
  passcode: string
  period: number
  created_at: string | null
  expires_at: number | null
  status: 'active' | 'expired' | 'cancelled' | 'unavailable'
}

export interface BaiduShareList {
  shares: BaiduShare[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  scope: 'account'
}

export type BaiduOperation = 'create-folder' | 'copy' | 'move' | 'rename' | 'delete' | 'file-meta' | 'semantic-search' | 'upload-url' | 'upload-text' | 'share-transfer'

export interface BaiduOperationPayload {
  paths?: string[]
  target_dir?: string
  name?: string
  query?: string
  url?: string
  password?: string
  content?: string
  filename?: string
  fsids?: string[]
  dir?: string
  confirmed?: boolean
}

export interface BaiduOperationResult {
  status?: string
  results?: BaiduOperationItemResult[]
  count?: number
  task_id?: string | number
  path?: string
  data?: unknown
  message?: string
  files?: BaiduFile[]
  return_url?: string
  return_markdown?: string
  agent_reply?: string
}

export interface BaiduOperationItemResult {
  status: string
  path?: string
  destination?: string
  message?: string
  return_url?: string
}

export interface BaiduUploadResult {
  results: { filename: string; status: 'success' | 'ok' | 'failed'; path?: string; fsid?: string; return_url?: string; message?: string }[]
  status: 'success' | 'partial' | 'failed'
  message: string
}

export interface BaiduCapabilities {
  provider: 'baidu'
  operations: string[]
  unsupported: Record<string, string>
  limits: Record<string, unknown>
}

export const getBaiduStatus = () => get<BaiduApiResponse<BaiduStatus>>(`${PREFIX}/status`, { timeout: 60000 })
export const getBaiduAuthConfig = () => get<BaiduApiResponse<BaiduAuthConfig>>(`${PREFIX}/auth/config`)
export const startBaiduAuthorization = () => post<BaiduApiResponse<BaiduAuthStart>>(`${PREFIX}/auth/start`, { confirmed: true })
export const getBaiduAuthSession = (sessionId: string) =>
  get<BaiduApiResponse<BaiduAuthSession>>(`${PREFIX}/auth/sessions/${encodeURIComponent(sessionId)}`)
export const cancelBaiduAuthSession = (sessionId: string) =>
  post<BaiduApiResponse<BaiduAuthSession>>(`${PREFIX}/auth/sessions/${encodeURIComponent(sessionId)}/cancel`)
export const revokeBaiduAuthorization = () =>
  post<BaiduApiResponse<{ connected: false; message: string }>>(`${PREFIX}/auth/revoke`, { confirmed: true })
export const getBaiduFiles = (params: { dir: string; keyword?: string; page?: number; page_size?: number; category?: BaiduCategory }) =>
  get<BaiduApiResponse<BaiduFileList>>(`${PREFIX}/files`, { params, timeout: 90000 })
export const getBaiduShares = (page = 1) =>
  get<BaiduApiResponse<BaiduShareList>>(`${PREFIX}/shares`, { params: { page }, timeout: 180000 })
export const getBaiduShareDetail = (shareId: string) =>
  get<BaiduApiResponse<{ share: Pick<BaiduShare, 'id' | 'share_url' | 'passcode' | 'status'> }>>(`${PREFIX}/shares/${encodeURIComponent(shareId)}`, { timeout: 180000 })
export const createBaiduShare = (payload: { paths: string[]; fsids?: string[]; title: string; period: 0 | 1 | 7 | 30; password?: string; confirmed: true }) =>
  post<BaiduApiResponse<{ share: BaiduShare; saved: boolean; message?: string }>>(`${PREFIX}/shares`, payload, { timeout: 180000 })
export const cancelBaiduShare = (shareId: string) =>
  post<BaiduApiResponse<{ cancelled: boolean; share_id: string }>>(`${PREFIX}/shares/${encodeURIComponent(shareId)}/cancel`, { confirmed: true }, { timeout: 180000 })
export const runBaiduOperation = (operation: BaiduOperation, payload: BaiduOperationPayload) =>
  post<BaiduApiResponse<BaiduOperationResult>>(`${PREFIX}/operations/${operation}`, payload, { timeout: 180000 })
export const getBaiduCapabilities = () => get<BaiduApiResponse<BaiduCapabilities>>(`${PREFIX}/capabilities`)
export const uploadBaiduFiles = (files: File[], dir: string) => {
  const form = new FormData()
  files.forEach(file => form.append('files', file))
  form.append('dir', dir)
  return post<BaiduApiResponse<BaiduUploadResult>>(`${PREFIX}/upload`, form, { timeout: 30 * 60 * 1000, headers: { 'Content-Type': undefined } })
}

export const downloadBaiduFile = async (path: string) => {
  // Renew the project's JSON authentication before a binary response, whose
  // business-error envelope is not visible to the shared JSON interceptor.
  const status = await getBaiduStatus()
  if (!status.success || !status.data?.connected) throw new Error(status.message || status.data?.message || '请先登录授权百度网盘')
  const response = await request.get<Blob>(`${PREFIX}/download`, {
    params: { path }, responseType: 'blob', timeout: 30 * 60 * 1000,
  })
  const disposition = response.headers['content-disposition']
  if (typeof disposition !== 'string' || !disposition) {
    let message = '未获得可下载文件，请核对服务端下载结果'
    try {
      const result = JSON.parse(await response.data.text())
      if (typeof result.message === 'string' && result.message) message = result.message
    } catch { /* Preserve the safe fallback for non-JSON responses. */ }
    throw new Error(message)
  }
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="([^"]+)"/i)?.[1] || disposition.match(/filename=([^;]+)/i)?.[1]
  let filename = plain?.trim() || ''
  if (encoded) {
    try { filename = decodeURIComponent(encoded.trim()) } catch { /* Keep the plain filename when encoding is invalid. */ }
  }
  return { blob: response.data, filename: filename.replace(/[\\/\x00-\x1f\x7f]/g, '_') }
}
