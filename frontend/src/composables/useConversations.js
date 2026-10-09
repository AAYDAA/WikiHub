import { ref } from 'vue'

const MAX_HISTORY = 20

/**
 * 历史会话的本地持久化。
 *
 * 存储 key 由调用方给（App.vue 里按游戏拼），因此不同游戏的会话互不干扰 ——
 * 不隔离的话，以撒的对话会出现在别的游戏的「历史」面板里。
 *
 * 换存储介质时只需要改这一个文件：小程序没有 localStorage，
 * 对应的是 uni.setStorageSync / uni.getStorageSync。
 *
 * @param {Function} getStoreKey 返回当前应使用的 storage key
 */
export function useConversations(getStoreKey) {
  const conversations = ref([])
  // 历史面板是否展开（纯 UI 状态，但只有这边需要，故一并放这）
  const historyPanel = ref(false)

  const load = () => {
    try {
      conversations.value = JSON.parse(localStorage.getItem(getStoreKey()) || '[]')
    } catch {
      conversations.value = []
    }
  }

  const save = () => {
    try {
      localStorage.setItem(
        getStoreKey(),
        JSON.stringify(conversations.value.slice(0, MAX_HISTORY)),
      )
    } catch (e) {
      console.warn('保存历史会话失败', e)
    }
  }

  /**
   * 把一次会话存进历史。
   * 没有任何用户发言就不存 —— 否则点一次「新对话」就会多一条空记录。
   *
   * 会话内容由调用方传入，而不是本模块自己去读 chat 的状态：
   * 这样本模块不依赖 useChat，两者可独立替换。
   */
  const archive = ({ messages, sessionId, qaMode }) => {
    const firstUser = (messages || []).find((m) => m.role === 'user')
    if (!firstUser) return

    conversations.value.unshift({
      id: sessionId || `local-${Date.now()}`,
      title: (firstUser.content || '对话').slice(0, 24),
      time: Date.now(),
      qaMode,
      sessionId,
      messages: JSON.parse(JSON.stringify(messages)),
    })
    save()
  }

  const deleteConversation = (idx) => {
    conversations.value.splice(idx, 1)
    save()
  }

  return { conversations, historyPanel, load, save, archive, deleteConversation }
}
