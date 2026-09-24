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

export function healthCheck() {
  return request.get('/health')
}
