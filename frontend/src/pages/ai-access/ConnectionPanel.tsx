import { useState } from 'react'
import { Copy, Download, ExternalLink, KeyRound } from 'lucide-react'
import type { McpClientConfig, McpConnection } from '@/api/mcpAccess'
import { useUIStore } from '@/store/uiStore'
import { copyToClipboard } from '@/utils/clipboard'
import { buttonClass, panelClass, primaryClass } from './shared'

export function ConnectionPanel({ connection, onOpenKeys }: { connection: McpConnection; onOpenKeys: () => void }) {
  const [client, setClient] = useState<'codex' | 'generic'>('codex')
  const addToast = useUIStore(state => state.addToast)
  const config = connection.configs[client]

  const copy = async (value: string, label: string) => {
    const copied = await copyToClipboard(value)
    addToast({ type: copied ? 'success' : 'error', message: copied ? `${label}已复制` : '复制失败，请选中文本手动复制' })
  }

  const downloadConfig = (value: McpClientConfig) => {
    const objectUrl = URL.createObjectURL(new Blob([value.content], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = value.filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  }

  return <div className="space-y-5">
    <section className={`${panelClass} divide-y divide-slate-200 dark:divide-slate-700`} aria-label="接入资源">
      <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
        <div className="min-w-0"><h2 className="font-semibold">接入说明链接</h2><p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">将这个链接交给 AI，读取安装和连接步骤。链接本身不授予业务权限。</p><a className="mt-2 block break-all text-sm text-blue-600 underline underline-offset-2 dark:text-blue-400" href={connection.landing_url} target="_blank" rel="noopener noreferrer">{connection.landing_url}</a></div>
        <div className="flex shrink-0 flex-wrap gap-2"><button type="button" className={buttonClass} onClick={() => void copy(connection.landing_url, '接入链接')}><Copy className="h-4 w-4" aria-hidden="true" />复制链接</button><a href={connection.guide_url} target="_blank" rel="noopener noreferrer" className={buttonClass}><ExternalLink className="h-4 w-4" aria-hidden="true" />AI 可读说明</a></div>
      </div>
      <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-start sm:justify-between sm:p-5">
        <div className="min-w-0"><h2 className="font-semibold">闲鱼运营 Skills</h2><p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">包含商品同步、选品上架、多网盘分享和卡券发货流程。安装方式见接入说明。</p><p className="mt-2 text-xs text-slate-500 dark:text-slate-400">版本 {connection.skill_version}</p><details className="mt-2 text-xs text-slate-500 dark:text-slate-400"><summary className="cursor-pointer py-1">查看文件校验值</summary><p className="mt-1 break-all font-mono leading-5">SHA-256: {connection.skill_sha256}</p></details></div>
        <a href={connection.skill_url} className={`${buttonClass} shrink-0 self-start`} download><Download className="h-4 w-4" aria-hidden="true" />下载 Skills</a>
      </div>
    </section>

    <section className={`${panelClass} p-4 sm:p-5`} aria-labelledby="mcp-config-heading">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 id="mcp-config-heading" className="font-semibold">MCP 客户端配置</h2><p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">先创建独立密钥，再将配置加入支持 Streamable HTTP 的 AI 客户端。</p></div><button type="button" className={primaryClass} onClick={onOpenKeys}><KeyRound className="h-4 w-4" aria-hidden="true" />管理客户端密钥</button></div>
      <div className="mt-4 flex flex-col gap-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-900/60 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><span className="text-xs text-slate-500 dark:text-slate-400">MCP 服务地址 · Bearer Token</span><p className="mt-1 break-all font-mono text-sm">{connection.endpoint}</p></div><button type="button" className={`${buttonClass} shrink-0 self-start`} onClick={() => void copy(connection.endpoint, 'MCP 地址')}><Copy className="h-4 w-4" aria-hidden="true" />复制地址</button></div>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3"><div className="flex gap-2" aria-label="客户端配置类型">{([['codex', 'Codex'], ['generic', '通用 JSON']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={client === value} onClick={() => setClient(value)} className={`min-h-[40px] rounded-lg px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${client === value ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300' : 'text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-700'}`}>{label}</button>)}</div><div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} onClick={() => void copy(config.content, '配置')}><Copy className="h-4 w-4" aria-hidden="true" />复制配置</button><button type="button" className={buttonClass} onClick={() => downloadConfig(config)}><Download className="h-4 w-4" aria-hidden="true" />下载配置</button></div></div>
      <pre tabIndex={0} aria-label={`${client === 'codex' ? 'Codex' : '通用 JSON'} 配置内容`} className="mt-3 max-h-96 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs leading-6 text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"><code>{config.content}</code></pre>
      <p className="mt-3 text-sm leading-6 text-slate-500 dark:text-slate-400">{client === 'codex' ? <>在启动客户端的环境中设置 <code className="font-mono text-xs">XIANYU_MCP_TOKEN</code>；配置文件通过环境变量读取密钥。</> : '通用配置使用密钥占位符，请在客户端的私密配置中替换。具体字段以客户端支持的配置格式为准。'} 密钥请勿放进聊天、接入链接或 Skills 文件。</p>
      <p className="mt-2 text-xs leading-6 text-slate-500 dark:text-slate-400">保存配置后，在 AI 客户端执行一次只读能力检查。本站密钥验证只检查授权，不代表客户端已连接、闲鱼账号已登录或发货流程已就绪。</p>
    </section>
  </div>
}
