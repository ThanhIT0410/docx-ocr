/** Coarse Vietnamese relative time for sidebar lists (§5 "Đã gửi gần đây"). */
export function formatRelativeTime(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)

  if (diffMin < 1) return 'Vừa xong'
  if (diffMin < 60) return `${diffMin} phút trước`

  const sameDay = d.toDateString() === now.toDateString()
  const time = d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
  if (sameDay) return `${time} hôm nay`

  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (d.toDateString() === yesterday.toDateString()) return `Hôm qua`

  return d.toLocaleDateString('vi-VN')
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString('vi-VN')
}
