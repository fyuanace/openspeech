export async function api(path, method = 'GET', body) {
  const response = await fetch('/admin/api/' + path, {
    method,
    cache: 'no-store',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail || '请求失败，请检查服务端状态。')
  return result
}
