<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { askQuestion, getGames } from './api/index.js'
import GameGrid from './components/GameGrid.vue'
import AddWikiModal from './components/AddWikiModal.vue'

// ===== 视图与游戏 =====
// view: home(选游戏) | chat(问答)；currentGame 决定用哪套知识库、以及会话存在哪
const view = ref('home')
const games = ref([])
const currentGame = ref(null)

const messages = ref([])
const inputText = ref('')
const loading = ref(false)
const messagesContainer = ref(null)
// 问答模式: rag=普通(快速) / agent=智能(可联网兜底)
const qaMode = ref('rag')
// 会话 id: 携带它以支持多轮上下文(用"它/这个"追问); 空=新会话
const sessionId = ref('')
// 历史会话: localStorage 持久化, 点「新对话」前自动存档, 可恢复
// 按游戏隔离 —— 不同游戏的对话存在不同 key 下, 不会串台
const storeKey = () => `wikihub_conversations_${currentGame.value?.id || 'default'}`
const MAX_HISTORY = 20
const conversations = ref([])
const historyPanel = ref(false)
// 首页「添加 Wiki」弹窗
const showAddWiki = ref(false)

const loadConversations = () => {
  try {
    conversations.value = JSON.parse(localStorage.getItem(storeKey()) || '[]')
  } catch {
    conversations.value = []
  }
}

const saveConversations = () => {
  try {
    localStorage.setItem(storeKey(), JSON.stringify(conversations.value.slice(0, MAX_HISTORY)))
  } catch (e) {
    console.warn('保存历史会话失败', e)
  }
}

const hasContent = () => messages.value.some((m) => m.role === 'user')

const archiveCurrent = () => {
  if (!hasContent()) return
  const firstUser = messages.value.find((m) => m.role === 'user')
  conversations.value.unshift({
    id: sessionId.value || `local-${Date.now()}`,
    title: (firstUser?.content || '对话').slice(0, 24),
    time: Date.now(),
    qaMode: qaMode.value,
    sessionId: sessionId.value,
    messages: JSON.parse(JSON.stringify(messages.value)),
  })
  saveConversations()
}

const newConversation = () => {
  archiveCurrent()
  sessionId.value = ''
  messages.value = []
  inputText.value = ''
  messages.value.push({
    role: 'assistant',
    content: `已开启新对话 🆕 继续问「${currentGame.value?.name || '游戏'}」的问题吧。`,
    sources: [], loading: false, id: 'new-session',
  })
}

const restoreConversation = (conv) => {
  messages.value = JSON.parse(JSON.stringify(conv.messages || []))
  sessionId.value = conv.sessionId || ''
  qaMode.value = conv.qaMode || 'rag'
  historyPanel.value = false
  scrollToBottom()
}

const deleteConversation = (idx) => {
  conversations.value.splice(idx, 1)
  saveConversations()
}

const fmtTime = (ts) => {
  const d = new Date(ts)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const suggestedQuestions = [
  '硫磺火道具有什么效果？',
  '怎么解锁 Lost 角色？',
  'Mega Satan 怎么打？',
  '以撒有哪些角色？',
]

const modeLabel = (m) => (m === 'agent' ? '智能' : '普通')

const scrollToBottom = async () => {
  await nextTick()
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

const sendQuestion = async (text) => {
  const question = (text || inputText.value).trim()
  if (!question || loading.value) return

  messages.value.push({ role: 'user', content: question })
  inputText.value = ''
  loading.value = true
  await scrollToBottom()

  const answerId = Date.now()
  messages.value.push({
    role: 'assistant', content: '', sources: [], loading: true, id: answerId,
  })

  try {
    const res = await askQuestion(question, currentGame.value?.id, qaMode.value, sessionId.value)
    const msg = messages.value.find((m) => m.id === answerId)
    if (msg) {
      msg.content = res.data.answer
      msg.sources = res.data.sources || []
      msg.mode = qaMode.value
      msg.loading = false
    }
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
  await scrollToBottom()
}

const handleKeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendQuestion()
  }
}

// ===== 游戏选择 / 进入 / 返回 =====
const loadGames = async () => {
  try {
    const res = await getGames()
    games.value = res.data.games || []
  } catch (e) {
    ElMessage.error('加载游戏列表失败，请确认后端已启动')
    console.warn(e)
  }
}

// 新 Wiki 登记成功后重新拉列表：它会以 ready=false（未建知识库）的状态出现
const onWikiAdded = () => {
  loadGames()
}

const pushWelcome = () => {
  messages.value.push({
    role: 'assistant',
    content: `你好！我是 WikiHub「${currentGame.value?.name || '游戏'}」攻略助手，问我这个游戏的问题吧 👇`,
    sources: [],
    loading: false,
    id: 'welcome',
  })
}

const enterGame = (game) => {
  if (game.ready === false) {
    ElMessage.warning(`「${game.name}」还没有知识库，索引建好后才能聊`)
    return
  }
  currentGame.value = game
  view.value = 'chat'
  sessionId.value = ''      // 换游戏 = 换会话，避免上下文串台
  messages.value = []
  loadConversations()       // 载入该游戏自己的历史会话
  pushWelcome()
  scrollToBottom()
}

const backHome = () => {
  archiveCurrent()
  view.value = 'home'
  messages.value = []
  sessionId.value = ''
}

onMounted(() => {
  loadGames()
})

function formatContent(text) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br/>')
}
</script>

<template>
  <div class="app-container">
    <header class="app-header">
      <div class="header-inner">
        <div class="logo">
          <span class="logo-icon">🎮</span>
          <span class="logo-text">WikiHub</span>
        </div>
        <button
          v-if="view === 'chat'"
          class="new-chat-btn"
          @click="backHome"
          title="返回游戏列表"
        >← 返回</button>
        <span class="subtitle">{{ view === 'chat' ? (currentGame?.name || '') : '游戏 Wiki 智能问答' }}</span>
        <button
          v-if="view === 'home'"
          class="new-chat-btn"
          @click="showAddWiki = true"
          title="粘贴一个 Wiki 地址，自动识别游戏名和图标"
        >＋ 添加 Wiki</button>
        <div v-if="view === 'chat'" class="mode-switch" title="普通模式：快速稳定；智能模式：检索不到会自动联网兜底">
          <span
            :class="['mode-opt', qaMode === 'rag' ? 'active' : '']"
            @click="qaMode = 'rag'"
          >普通</span>
          <span
            :class="['mode-opt', qaMode === 'agent' ? 'active' : '']"
            @click="qaMode = 'agent'"
          >智能</span>
        </div>
        <button v-if="view === 'chat'" class="new-chat-btn" @click="newConversation" title="开启新对话（当前对话将存档）">
          ✚ 新对话
        </button>
        <button v-if="view === 'chat'" class="new-chat-btn" @click="historyPanel = !historyPanel" title="查看历史会话">
          🕘 历史
        </button>
      </div>
    </header>

    <!-- 首页：游戏卡片网格 -->
    <GameGrid v-if="view === 'home'" :games="games" @select="enterGame" />

    <!-- 聊天页 -->
    <template v-else>
    <div v-if="historyPanel" class="history-overlay" @click.self="historyPanel = false">
      <div class="history-panel">
        <div class="history-header">
          <span>历史会话（{{ conversations.length }}）</span>
          <span class="history-close" @click="historyPanel = false">✕</span>
        </div>
        <div v-if="!conversations.length" class="history-empty">
          暂无历史会话。点「✚ 新对话」会保存当前对话到这里。
        </div>
        <div
          v-for="(conv, idx) in conversations"
          :key="conv.id + idx"
          class="history-item"
          @click="restoreConversation(conv)"
        >
          <div class="history-item-main">
            <div class="history-item-title">{{ conv.title }}</div>
            <div class="history-item-meta">
              {{ fmtTime(conv.time) }} · {{ conv.messages.length }} 条 · {{ conv.qaMode === 'agent' ? '智能' : '普通' }}
            </div>
          </div>
          <span class="history-item-del" @click.stop="deleteConversation(idx)">🗑</span>
        </div>
      </div>
    </div>

    <main class="app-main">
      <div class="chat-container">
        <div class="messages" ref="messagesContainer">
          <div
            v-for="(msg, index) in messages"
            :key="index"
            :class="['message', msg.role]"
          >
            <div class="avatar">{{ msg.role === 'user' ? '我' : '🎮' }}</div>
            <div class="bubble-wrap">
              <div class="bubble">
                <div v-if="msg.loading" class="typing">
                  <span></span><span></span><span></span>
                </div>
                <div v-else class="bubble-content" v-html="formatContent(msg.content)"></div>
              </div>

              <div v-if="msg.mode" class="msg-mode-tag">
                {{ msg.mode === 'agent' ? '🤖 智能模式' : '⚡ 普通模式' }}
              </div>

              <div v-if="msg.sources && msg.sources.length" class="sources">
                <div class="sources-title">参考来源</div>
                <a
                  v-for="s in msg.sources"
                  :key="s.url"
                  :href="s.url"
                  target="_blank"
                  rel="noopener"
                  class="source-item"
                >
                  <span class="source-icon">📄</span>
                  <span class="source-text">{{ s.section || s.title }}</span>
                </a>
              </div>
            </div>
          </div>
        </div>

        <div v-if="messages.length <= 1" class="suggestions">
          <div class="suggestions-title">试试问这些问题：</div>
          <div class="suggestion-list">
            <div
              v-for="q in suggestedQuestions"
              :key="q"
              class="suggestion-item"
              @click="sendQuestion(q)"
            >
              {{ q }}
            </div>
          </div>
        </div>
      </div>

      <div class="input-area">
        <div class="input-wrap">
          <textarea
            v-model="inputText"
            @keydown="handleKeydown"
            placeholder="问我任何游戏攻略问题..."
            rows="1"
            class="chat-input"
          />
          <button
            class="send-btn"
            :disabled="!inputText.trim() || loading"
            @click="sendQuestion()"
          >
            发送
          </button>
        </div>
        <div class="input-tip">基于 Wiki 知识库回答 · 答案仅供参考</div>
      </div>
    </main>
    </template>

    <!-- 添加 Wiki：解析 → 预览确认 → 登记（首页按钮触发） -->
    <AddWikiModal v-model="showAddWiki" @added="onWikiAdded" />
  </div>
</template>

<style scoped>
.app-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #f5f7fb;
}

.app-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  flex-shrink: 0;
}

.header-inner {
  max-width: 860px;
  margin: 0 auto;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 20px;
}

.logo-icon {
  font-size: 24px;
}

.subtitle {
  font-size: 13px;
  opacity: 0.85;
  margin-left: auto;
}

.mode-switch {
  display: flex;
  gap: 2px;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  padding: 3px;
  font-size: 13px;
  cursor: pointer;
  user-select: none;
}

.mode-opt {
  padding: 4px 14px;
  border-radius: 999px;
  color: rgba(255, 255, 255, 0.85);
  transition: all 0.2s;
}

.mode-opt.active {
  background: white;
  color: #667eea;
  font-weight: 600;
}

.new-chat-btn {
  margin-left: 8px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.35);
  color: white;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}

.new-chat-btn:hover {
  background: rgba(255, 255, 255, 0.32);
}

.history-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 100;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 64px;
}

.history-panel {
  background: white;
  border-radius: 14px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.18);
  width: 420px;
  max-width: 92vw;
  max-height: 65vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  font-weight: 600;
  font-size: 14.5px;
  color: #1f2937;
  border-bottom: 1px solid #f0f0f0;
}

.history-close {
  cursor: pointer;
  color: #9ca3af;
  font-size: 14px;
  padding: 2px 6px;
}

.history-close:hover {
  color: #4b5563;
}

.history-empty {
  padding: 32px 16px;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid #f7f7f7;
  transition: background 0.15s;
}

.history-item:hover {
  background: #f5f3ff;
}

.history-item-main {
  flex: 1;
  min-width: 0;
}

.history-item-title {
  font-size: 14px;
  color: #374151;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-item-meta {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 2px;
}

.history-item-del {
  cursor: pointer;
  color: #c4c4c4;
  font-size: 14px;
  padding: 4px;
  flex-shrink: 0;
}

.history-item-del:hover {
  color: #ef4444;
}

.msg-mode-tag {
  font-size: 11px;
  color: #9ca3af;
  padding-left: 2px;
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
  padding: 16px;
  min-height: 0;
}

.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 10px 4px 20px;
  min-height: 0;
}

.message {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.message.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #667eea;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}

.message.assistant .avatar {
  background: #764ba2;
  font-size: 18px;
}

.bubble-wrap {
  max-width: 75%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.message.user .bubble-wrap {
  align-items: flex-end;
}

.bubble {
  background: white;
  padding: 12px 16px;
  border-radius: 14px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  line-height: 1.65;
  font-size: 14.5px;
  word-break: break-word;
}

.message.user .bubble {
  background: #667eea;
  color: white;
}

.bubble-content {
  white-space: pre-wrap;
}

.typing {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}

.typing span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #9ca3af;
  animation: bounce 1.2s infinite ease-in-out;
}

.typing span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
  40% { transform: scale(1); opacity: 1; }
}

.sources {
  background: rgba(102, 126, 234, 0.06);
  border: 1px solid rgba(102, 126, 234, 0.15);
  border-radius: 10px;
  padding: 8px 12px;
  max-width: 100%;
}

.sources-title {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 6px;
  font-weight: 500;
}

.source-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  color: #4b5563;
  text-decoration: none;
  font-size: 12.5px;
  padding: 3px 0;
  line-height: 1.4;
}

.source-item:hover {
  color: #667eea;
}

.source-icon {
  flex-shrink: 0;
}

.source-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.suggestions {
  padding: 10px 4px 20px;
}

.suggestions-title {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 10px;
}

.suggestion-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.suggestion-item {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 18px;
  padding: 6px 14px;
  font-size: 13px;
  color: #4b5563;
  cursor: pointer;
  transition: all 0.2s;
}

.suggestion-item:hover {
  border-color: #667eea;
  color: #667eea;
  background: #f5f3ff;
}

.input-area {
  flex-shrink: 0;
  padding-top: 10px;
}

.input-wrap {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  background: white;
  border-radius: 16px;
  padding: 10px 12px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  border: 1px solid #e5e7eb;
  transition: border-color 0.2s;
}

.input-wrap:focus-within {
  border-color: #667eea;
}

.chat-input {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  font-size: 14.5px;
  line-height: 1.5;
  font-family: inherit;
  max-height: 120px;
  background: transparent;
  color: #1f2937;
}

.send-btn {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 10px;
  padding: 8px 20px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s;
  flex-shrink: 0;
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.input-tip {
  text-align: center;
  font-size: 11.5px;
  color: #9ca3af;
  margin-top: 6px;
}

@media (max-width: 640px) {
  .header-inner {
    padding: 12px 16px;
  }
  .subtitle {
    display: none;
  }
  .app-main {
    padding: 12px;
  }
  .bubble-wrap {
    max-width: 82%;
  }
  .bubble {
    padding: 10px 14px;
    font-size: 14px;
  }
  .suggestion-item {
    font-size: 12.5px;
    padding: 5px 12px;
  }
}
</style>
