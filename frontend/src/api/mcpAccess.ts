import { del, get, post } from '@/utils/request'

const PREFIX = '/api/v1/mcp'

interface McpResponse<T> {
  success: boolean
  data?: T
  message?: string
  detail?: string
}

export interface McpScope {
  id: string
  label: string
  description: string
}

export interface McpClientConfig {
  filename: string
  language: string
  content: string
}

export interface McpConnection {
  endpoint: string
  guide_url: string
  landing_url: string
  skill_url: string
  skill_version: string
  skill_sha256: string
  auth_type: 'personal-bearer-token'
  scopes: McpScope[]
  configs: { codex: McpClientConfig; generic: McpClientConfig }
}

export interface McpToken {
  id: string
  name: string
  scopes: string[]
  expires_at: string
  revoked: boolean
  created_at?: string | null
  revoked_at?: string | null
  last_used_at?: string | null
}

export interface McpCreatedToken extends McpToken {
  token: string
  message: string
}

export interface McpConnectionCheck {
  valid: boolean
  scopes: string[]
  tool_count: number
  checked_at: string
  message: string
}

export interface McpCallLog {
  id: string
  token_id: string
  tool: string
  success: boolean
  duration_ms: number
  error_code: string | null
  created_at: string
}

export interface McpCallPage extends McpResponse<McpCallLog[]> {
  total: number
  page: number
  page_size: number
}

async function dataOf<T>(response: Promise<McpResponse<T>>): Promise<T> {
  const result = await response
  if (!result.success || result.data === undefined) {
    throw new Error(result.message || result.detail || '请求未成功，请重试')
  }
  return result.data
}

export const getMcpConnection = () => dataOf(get<McpResponse<McpConnection>>(`${PREFIX}/connection`))
export const getMcpTokens = () => dataOf(get<McpResponse<McpToken[]>>(`${PREFIX}/tokens`))
export const createMcpToken = (body: { name: string; scopes: string[]; expires_in_days: number }) =>
  dataOf(post<McpResponse<McpCreatedToken>>(`${PREFIX}/tokens`, body))
export const checkMcpConnection = (token: string) =>
  dataOf(post<McpResponse<McpConnectionCheck>>(`${PREFIX}/connection/check`, { token }))

export async function revokeMcpToken(id: string): Promise<void> {
  const result = await del<McpResponse<never>>(`${PREFIX}/tokens/${encodeURIComponent(id)}`)
  if (!result.success) throw new Error(result.message || result.detail || '撤销失败，请重试')
}

export async function getMcpLogs(page = 1, pageSize = 20): Promise<McpCallPage> {
  const result = await get<McpCallPage>(`${PREFIX}/logs`, { params: { page, page_size: pageSize } })
  if (!result.success || !result.data) throw new Error(result.message || '调用记录加载失败')
  return result
}
