import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getGames } from '../api/index.js'

/**
 * 游戏列表 + 当前进入的游戏 + 视图状态。
 *
 * 只回答「有哪些游戏、现在在哪个游戏里」，完全不碰聊天内容。
 * 换 UI 层（uni-app / 小程序）时本文件可整包复用。
 */
export function useGames() {
  const games = ref([])
  const currentGame = ref(null)
  // home = 选游戏；chat = 问答
  const view = ref('home')

  const loadGames = async () => {
    try {
      const res = await getGames()
      games.value = res.data.games || []
    } catch (e) {
      ElMessage.error('加载游戏列表失败，请确认后端已启动')
      console.warn(e)
    }
  }

  return { games, currentGame, view, loadGames }
}
