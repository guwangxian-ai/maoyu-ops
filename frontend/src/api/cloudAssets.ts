import request, { get, post } from '@/utils/request'

const PREFIX = '/api/v1/cloud-drive/assets'

export type CloudProvider = 'quark' | 'baidu'
export type CloudAssetStatus = 'staged' | 'importing' | 'syncing' | 'partial' | 'ready' | 'sharing' | 'deliverable' | 'failed' | 'interrupted'
export type CloudTargetStatus = 'pending' | 'uploading' | 'uploaded' | 'sharing' | 'shared' | 'failed' | 'unknown'

export interface CloudAssetResponse<T> {
  success: boolean
  message?: string
  data?: T
}

export interface CloudProviderStatus {
  provider: CloudProvider
  connected: boolean
  verified: boolean
  message?: string
  share_notice?: string
}

export interface CloudAssetAvailability {
  providers: CloudProviderStatus[]
  limits: Record<string, unknown>
}

export interface CloudAssetFile {
  name: string
  size: number
  sha256: string
}

export interface CloudAssetTarget {
  provider: CloudProvider
  status: CloudTargetStatus
  message?: string
  files: CloudAssetTargetFile[]
  share_url?: string
  passcode?: string
  expires_at?: string | number | null
}

export interface CloudAssetTargetFile extends CloudAssetFile {
  verification: string
  fid?: string
  fsid?: string
  path?: string
}

export interface CloudAssetAnalysis {
  method: string
  ai_generated: boolean
  file_count: number
  total_bytes: number
  files: (CloudAssetFile & { text: string; extraction: string; truncated?: boolean })[]
  message?: string
}

export interface CloudAsset {
  id: string
  name: string
  version: number
  published_version: number | null
  deliverable: boolean
  status: CloudAssetStatus
  message?: string
  files: CloudAssetFile[]
  targets: CloudAssetTarget[]
  delivery_content: string
  card_id?: string | null
  created_at: string | number
  updated_at: string | number
  analysis?: CloudAssetAnalysis | null
}

export interface CloudAssetPage {
  list: CloudAsset[]
  total: number
  page: number
  page_size: number
}

export const getCloudAssetAvailability = () =>
  get<CloudAssetResponse<CloudAssetAvailability>>(`${PREFIX}/status`, { timeout: 120000 })
export const getCloudAssets = (page = 1, pageSize = 20) =>
  get<CloudAssetResponse<CloudAssetPage>>(`${PREFIX}/`, { params: { page, page_size: pageSize } })
export const getCloudAsset = (id: string) =>
  get<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}`)
export const uploadCloudAsset = (name: string, providers: CloudProvider[], files: File[]) => {
  const form = new FormData()
  form.append('name', name)
  form.append('providers', JSON.stringify(providers))
  form.append('confirmed', 'true')
  files.forEach(file => form.append('files', file))
  return post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/upload`, form, {
    timeout: 30 * 60 * 1000, headers: { 'Content-Type': undefined },
  })
}
export const importCloudAsset = (payload: { name: string; provider: CloudProvider; providers?: CloudProvider[]; file_ids?: string[]; paths?: string[]; confirmed: true }) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/import`, payload, { timeout: 30 * 60 * 1000 })
export const importCloudAssetUrl = (payload: { name: string; url: string; filename: string; providers: CloudProvider[]; confirmed: true }) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/import-url`, payload, { timeout: 30 * 60 * 1000 })
export const importCloudAssetShare = (payload: { name: string; provider: CloudProvider; providers: CloudProvider[]; url: string; password?: string; confirmed: true }) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/import-share`, payload, { timeout: 180000 })
export const uploadCloudAssetVersion = (id: string, files: File[]) => {
  const form = new FormData()
  form.append('confirmed', 'true')
  files.forEach(file => form.append('files', file))
  return post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}/versions`, form, {
    timeout: 30 * 60 * 1000, headers: { 'Content-Type': undefined },
  })
}
export const syncCloudAsset = (id: string) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}/sync`, { confirmed: true }, { timeout: 180000 })
export const shareCloudAsset = (id: string, period: 0 | 1 | 7 | 30) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}/shares`, { confirmed: true, period }, { timeout: 180000 })
export const createCloudAssetCard = (id: string, itemIds?: string[]) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}/card`, { confirmed: true, ...(itemIds?.length ? { item_ids: itemIds } : {}) }, { timeout: 180000 })
export const renameCloudAsset = async (id: string, name: string) => {
  const result = await request.patch<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}`, { name, confirmed: true })
  return result.data
}
export const analyzeCloudAsset = (id: string) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}/analyze`, { confirmed: true }, { timeout: 180000 })
export const retryCloudAsset = (id: string) =>
  post<CloudAssetResponse<CloudAsset>>(`${PREFIX}/${encodeURIComponent(id)}/retry`, { confirmed: true }, { timeout: 180000 })
