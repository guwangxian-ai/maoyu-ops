// 同一 IP 下的多个项目共享浏览器 Storage；子路径部署时给本项目的键加前缀。
const basePath = import.meta.env.BASE_URL.replace(/^\/+|\/+$/g, '').replace(/\//g, ':')
// 修改公网路径时可保留同一实例原来的命名空间，避免丢失登录态和用户偏好。
const namespace = import.meta.env.VITE_STORAGE_NAMESPACE?.trim() || basePath
const prefix = namespace ? `${namespace}:` : ''

interface ScopedStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

function scopeStorage(getStorage: () => Storage): ScopedStorage {
  return {
    getItem: (key) => getStorage().getItem(`${prefix}${key}`),
    setItem: (key, value) => getStorage().setItem(`${prefix}${key}`, value),
    removeItem: (key) => getStorage().removeItem(`${prefix}${key}`),
  }
}

export const scopedLocalStorage = scopeStorage(() => window.localStorage)
export const scopedSessionStorage = scopeStorage(() => window.sessionStorage)
