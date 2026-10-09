import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { askQuestion } from '../api/index.js'

/**
 * 单次会话的问答逻辑：消息列表、发送、模式、多轮上下文。
 *
 * 不负责历史存档（那是 useConversations 的事），也不碰任何 DOM。
 *
 * @param {object}   opts
 * @param {Function} opts.getGameId   取当前游戏 id（决定用哪套知识库）
 * @param {Function} opts.getGameName 取当前游戏名（仅用于欢迎语文案）
 * @param {Function} opts.onUpdate    消息变化后的回调，供 UI 层滚动到底部。
 *                                    滚动是视图职责，故用回调注入而非写在这里。
 */
export function useChat({ getGameId, getGameName, onUpdate } = {}) {
  const messages = ref([])
  const inputText = ref('')
  const loading = ref(false)
  // 后端返回的会话 id：带上它才能让"它/这个"这类追问有上下文；空 = 新会话
  const sessionId = ref('')
  // rag = 普通（快速稳定）｜agent = 智能（检索不到会联网兜底）
  const qaMode = ref('rag')

  const pushWelcome = () => {
    messages.value.push({
      role: 'assistant',
      content: `你好！我是 WikiHub「${getGameName?.() || '游戏'}」攻略助手，问我这个游戏的问题吧 👇`,
      sources: [],
      loading: false,
      id: 'welcome',
    })
  }

  /** 清空当前会话（不存档）。换游戏 / 返回首页 / 开新对话时调用。 */
  const reset = () => {
    messages.value = []
    inputText.value = ''
    sessionId.value = ''
  }

  const sendQuestion = async (text) => {
    const question = (text || inputText.value).trim()
    if (!question || loading.value) return

    messages.value.push({ role: 'user', content: question })
    inputText.value = ''
    loading.value = true

    const answerId = Date.now()
    messages.value.push({
      role: 'assistant', content: '', sources: [], loading: true, id: answerId,
    })
    onUpdate?.()

    try {
      const res = await askQuestion(question, getGameId?.(), qaMode.value, sessionId.value)
      const msg = messages.value.find((m) => m.id === answerId)
      if (msg) {
        msg.content = res.data.answer
        msg.sources = res.data.sources || []
        msg.mode = qaMode.value
        msg.loading = false
      }
      // 记住后端下发的 session_id，下一轮追问才带得上上下文
      if (res.data.session_id) sessionId.value = res.data.session_id
    } catch (e) {
      const msg = messages.value.find((m) => m.id === answerId)
      if (msg) {
        msg.content = '抱歉，出了点问题，请稍后再试。'
        msg.loading = false
      }
      ElMessage.error(e.response?.data?.detail || e.message || '请求失败')
    }

    loading.value = false
    onUpdate?.()
  }

  return { messages, inputText, loading, sessionId, qaMode, sendQuestion, reset, pushWelcome }
}
