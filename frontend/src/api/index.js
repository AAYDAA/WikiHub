import axios from 'axios'

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  timeout: 60000,
})

export function askQuestion(question, game, mode, sessionId) {
  return request.post('/api/ask', { question, game, mode, session_id: sessionId })
}

export function searchDocs(query, top_k = 5, game) {
  return request.post('/api/search', { query, top_k, game })
}

export function getGames() {
  return request.get('/api/games')
}

// 探测 Wiki 地址（只解析预览，不落库）
export function probeWiki(url) {
  return request.post('/api/wiki/probe', { url })
}

// 用户确认后登记到后端 games.json
export function registerWiki(payload) {
  return request.post('/api/wiki/register', payload)
}

// 一键建库：启动后台任务 + 轮询进度（建库要几十分钟，故拆成两个接口，
// 不能让一个请求一直挂着等它跑完）
export function buildGame(gameId) {
  return request.post(`/api/games/${gameId}/build`)
}

export function getBuildStatus(gameId) {
  return request.get(`/api/games/${gameId}/build/status`)
}

export function healthCheck() {
  return request.get('/health')
}
