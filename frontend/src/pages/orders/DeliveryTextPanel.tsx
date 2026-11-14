import { useState } from 'react'
import { getOrderDetail, retryDeliveryText, type OrderDetail } from '@/api/orders'

export const deliveryTextLabel = (state: string) => ({
  confirming: '正在提交发货凭证', shipping_unknown: '凭证结果待核实',
  pending: '等待发送', sending: '发送中', success: '平台已确认发送',
  failed: '发送失败', unknown: '发送结果待核实', cancelled: '已停止发送',
}[state] || state)

export function DeliveryTextPanel({ order, onRefresh }: { order: OrderDetail; onRefresh: (order: OrderDetail) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const receipt = order.delivery_text
  if (!receipt) return null
  const refresh = async (retry = false) => {
    setBusy(true)
    setError(null)
    try {
      if (retry) {
        const result = await retryDeliveryText(order.order_id)
        if (!result.success) throw new Error(result.message || '重新发送失败')
      }
      const result = await getOrderDetail(order.order_id)
      if (!result.success || !result.data) throw new Error('读取发送记录失败')
      onRefresh(result.data)
    } catch (error) {
      setError(error instanceof Error ? error.message : '读取发送记录失败')
    } finally { setBusy(false) }
  }
  return <section aria-label="发货后的文字消息" className="space-y-2 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium">发货后的文字消息</h3>
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={() => refresh()} className="btn-ios-secondary">{busy ? '处理中…' : '刷新记录'}</button>
        {receipt.can_retry && <button type="button" disabled={busy} onClick={() => refresh(true)} className="btn-ios-primary">重试文字发送</button>}
      </div>
    </div>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    <p>{deliveryTextLabel(receipt.state)} · 已尝试 {receipt.attempts} 次</p>
    <p className="text-gray-500">最近更新：{receipt.updated_at.replace('T', ' ')}</p>
    {receipt.reason && <p className="text-amber-700 dark:text-amber-400">{receipt.reason}</p>}
    <p className="text-gray-500">发货凭证保留在订单中。平台发送回执不代表买家已读；结果待核实时不会自动重发。</p>
    {!!receipt.history?.length && <ol className="space-y-2">
      {receipt.history.map(attempt => <li key={attempt.number} className="border-t border-gray-100 dark:border-gray-700 pt-2">
        第 {attempt.number} 次 · {deliveryTextLabel(attempt.state)} · {(attempt.finished_at || attempt.started_at).replace('T', ' ')}
        {attempt.reason && <p className="text-gray-500">{attempt.reason}</p>}
      </li>)}
    </ol>}
  </section>
}
