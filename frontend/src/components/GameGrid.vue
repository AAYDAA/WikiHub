<script setup>
/**
 * 游戏卡片网格（首页）
 *
 * 图标 / 名字的可编辑逻辑：
 *   默认值来自后端 /api/games（games.json）；用户改过的存在 localStorage 覆盖默认，
 *   所以「不改就是默认的」，改了也不用改后端。
 *
 * 图片为什么走 base64 而不是上传后端：
 *   localStorage 只有 ~5MB，因此上传的图片先用 canvas 压到 128px 再转 base64，
 *   单张通常几 KB，十几张游戏图标也不会撑爆。
 */
import { onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { buildGame, getBuildStatus } from '../api/index.js'

const props = defineProps({
  games: { type: Array, default: () => [] },
})
const emit = defineEmits(['select', 'refresh'])

const OVERRIDE_KEY = 'wikihub_game_overrides'
const ICON_MAX_PX = 128

const overrides = ref(loadOverrides())
const editing = ref(null)

function loadOverrides() {
  try {
    return JSON.parse(localStorage.getItem(OVERRIDE_KEY) || '{}')
  } catch {
    return {}
  }
}

function saveOverrides() {
  try {
    localStorage.setItem(OVERRIDE_KEY, JSON.stringify(overrides.value))
  } catch (e) {
    ElMessage.error('本地存储写入失败，图片可能过大')
    console.warn(e)
  }
}

// 生效值：用户覆盖 > 后端默认
const displayName = (g) => overrides.value[g.id]?.name || g.name
const displayIcon = (g) => overrides.value[g.id]?.icon || g.icon
const isImageIcon = (v) => typeof v === 'string' && (v.startsWith('data:') || v.startsWith('http'))

function openEdit(g, e) {
  e?.stopPropagation()
  editing.value = {
    id: g.id,
    name: displayName(g),
    icon: displayIcon(g),
    isCustom: !!overrides.value[g.id],
  }
}

function saveEdit() {
  const { id, name, icon } = editing.value
  overrides.value[id] = { name: (name || '').trim(), icon }
  saveOverrides()
  editing.value = null
  ElMessage.success('已保存')
}

function resetEdit(id) {
  delete overrides.value[id]
  saveOverrides()
  editing.value = null
  ElMessage.success('已恢复默认')
}

function onPickFile(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = '' // 允许重复选同一个文件
  if (!file) return
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('请选择图片文件')
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    const img = new Image()
    img.onload = () => {
      // 等比缩放到最长边 128px，再转 base64，避免撑爆 localStorage
      const scale = Math.min(1, ICON_MAX_PX / Math.max(img.width, img.height))
      const w = Math.max(1, Math.round(img.width * scale))
      const h = Math.max(1, Math.round(img.height * scale))
      const canvas = document.createElement('canvas')
      canvas.width = w
      canvas.height = h
      canvas.getContext('2d').drawImage(img, 0, 0, w, h)
      try {
        editing.value.icon = canvas.toDataURL('image/webp', 0.85)
      } catch {
        editing.value.icon = canvas.toDataURL('image/png')
      }
    }
    img.onerror = () => ElMessage.error('图片解析失败')
    img.src = reader.result
  }
  reader.readAsDataURL(file)
}

// ===== 一键建库 =====
// 建库是后台任务（几十分钟），前端只负责：启动 → 轮询进度 → 完成后通知父组件刷新。
// 轮询器存在 map 里，组件卸载时统一清掉，避免定时器泄漏。
const buildJobs = ref({})   // { gameId: { percent, message, status } }
const timers = {}

const stopPoll = (id) => {
  if (timers[id]) {
    clearInterval(timers[id])
    delete timers[id]
  }
}

const pollBuild = (id) => {
  timers[id] = setInterval(async () => {
    try {
      const res = await getBuildStatus(id)
      buildJobs.value[id] = res.data
      if (res.data.status === 'done') {
        stopPoll(id)
        ElMessage.success('知识库已建好，可以开始问答了')
        emit('refresh')
      } else if (res.data.status === 'failed') {
        stopPoll(id)
        ElMessage.error(res.data.message || '构建失败')
      }
    } catch (e) {
      // 轮询偶发失败不放弃：让下一轮继续试（长任务期间网络抖动很常见）
      console.warn('查询建库进度失败', e)
    }
  }, 3000)
}

async function startBuild(g, e) {
  e?.stopPropagation()
  if (buildJobs.value[g.id]?.status === 'running') return
  try {
    const res = await buildGame(g.id)
    buildJobs.value[g.id] = res.data.job
    ElMessage.info(`已开始抓取「${g.name}」的 Wiki，这一步比较久，可以先去忙别的`)
    pollBuild(g.id)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message || '启动失败')
  }
}

onUnmounted(() => Object.keys(timers).forEach(stopPoll))
</script>

<template>
  <div class="home">
    <div class="home-head">
      <h1 class="home-title">选择要聊的游戏</h1>
      <p class="home-sub">点卡片进入问答 · 卡片右上角 ✎ 可改图标和名字 · 顶栏「＋ 添加 Wiki」可新增游戏</p>
    </div>

    <div class="grid">
      <div
        v-for="g in props.games"
        :key="g.id"
        class="card"
        :class="{ disabled: g.ready === false }"
        @click="emit('select', g)"
      >
        <button class="card-edit" title="编辑图标和名字" @click="openEdit(g, $event)">✎</button>

        <div class="card-icon">
          <img v-if="isImageIcon(displayIcon(g))" :src="displayIcon(g)" alt="" />
          <span v-else class="emoji">{{ displayIcon(g) }}</span>
        </div>
        <div class="card-name">{{ displayName(g) }}</div>

        <!-- 没索引的卡片：给「建知识库」入口；构建中就地显示进度 -->
        <template v-if="g.ready === false">
          <div v-if="buildJobs[g.id]?.status === 'running'" class="build-progress">
            <div class="bar"><span :style="{ width: (buildJobs[g.id].percent || 0) + '%' }"></span></div>
            <div class="build-msg">{{ buildJobs[g.id].message }}</div>
          </div>
          <button v-else class="build-btn" @click="startBuild(g, $event)">建知识库</button>
        </template>
      </div>
    </div>

    <!-- 编辑弹窗（自绘，少引一个组件依赖） -->
    <div v-if="editing" class="modal-mask" @click.self="editing = null">
      <div class="modal">
        <div class="modal-head">
          <span>编辑「{{ displayName({ id: editing.id, name: editing.name }) }}」</span>
          <span class="modal-close" @click="editing = null">✕</span>
        </div>

        <div class="modal-body">
          <label class="field-label">名字</label>
          <input v-model="editing.name" class="field-input" maxlength="20" placeholder="给这个游戏起个名字" />

          <label class="field-label">图标</label>
          <div class="icon-row">
            <div class="icon-preview">
              <img v-if="isImageIcon(editing.icon)" :src="editing.icon" alt="" />
              <span v-else class="emoji">{{ editing.icon }}</span>
            </div>
            <div class="icon-actions">
              <label class="upload-btn">
                上传图片
                <input type="file" accept="image/*" hidden @change="onPickFile" />
              </label>
              <span class="icon-tip">自动压缩到 {{ ICON_MAX_PX }}px 后保存在本地</span>
            </div>
          </div>
        </div>

        <div class="modal-foot">
          <button v-if="editing.isCustom" class="btn ghost" @click="resetEdit(editing.id)">恢复默认</button>
          <span class="spacer"></span>
          <button class="btn" @click="editing = null">取消</button>
          <button class="btn primary" @click="saveEdit">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home {
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
  padding: 32px 20px;
}

.home-head {
  text-align: center;
  margin-bottom: 28px;
}

.home-title {
  font-size: 24px;
  font-weight: 700;
  color: #1f2937;
  margin: 0 0 8px;
}

.home-sub {
  font-size: 13.5px;
  color: #6b7280;
  margin: 0;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 18px;
}

.card {
  position: relative;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 22px 12px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  transition: transform 0.18s, box-shadow 0.18s, border-color 0.18s;
}

.card:hover {
  transform: translateY(-3px);
  border-color: #c7b8f7;
  box-shadow: 0 10px 24px rgba(102, 126, 234, 0.16);
}

.card.disabled {
  opacity: 0.55;
}

.card-icon {
  width: 72px;
  height: 72px;
  border-radius: 16px;
  background: #f3f0ff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
}

.card-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-icon .emoji {
  font-size: 38px;
  line-height: 1;
}

.card-name {
  font-size: 14.5px;
  font-weight: 600;
  color: #374151;
  text-align: center;
  word-break: break-all;
}

.card-badge {
  position: absolute;
  bottom: 8px;
  font-size: 11px;
  color: #b45309;
  background: #fef3c7;
  border-radius: 999px;
  padding: 1px 8px;
}

/* ---- 未建库卡片的「建知识库」入口与进度 ---- */
.build-btn {
  border: 1px solid #d8d2f5;
  background: #f7f5ff;
  color: #667eea;
  border-radius: 9px;
  padding: 5px 14px;
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}

.build-btn:hover {
  background: #efeaff;
  border-color: #c7b8f7;
}

.build-progress {
  width: 100%;
  padding: 0 4px;
}

.build-progress .bar {
  height: 6px;
  background: #eef0f6;
  border-radius: 999px;
  overflow: hidden;
}

.build-progress .bar span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #667eea, #764ba2);
  border-radius: 999px;
  transition: width 0.4s ease;
}

.build-msg {
  margin-top: 6px;
  font-size: 11px;
  color: #6b7280;
  text-align: center;
  line-height: 1.35;
  word-break: break-all;
}

.card-edit {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: none;
  background: #f3f4f6;
  color: #6b7280;
  font-size: 13px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}

.card:hover .card-edit {
  opacity: 1;
}

.card-edit:hover {
  background: #ede9fe;
  color: #667eea;
}

/* ---- 编辑弹窗 ---- */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.modal {
  background: white;
  border-radius: 14px;
  width: 400px;
  max-width: 100%;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.2);
  overflow: hidden;
}

.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  font-weight: 600;
  font-size: 14.5px;
  color: #1f2937;
  border-bottom: 1px solid #f0f0f0;
}

.modal-close {
  cursor: pointer;
  color: #9ca3af;
}

.modal-close:hover {
  color: #4b5563;
}

.modal-body {
  padding: 16px;
}

.field-label {
  display: block;
  font-size: 12.5px;
  color: #6b7280;
  margin-bottom: 6px;
}

.field-input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #e5e7eb;
  border-radius: 9px;
  padding: 9px 12px;
  font-size: 14px;
  outline: none;
  margin-bottom: 16px;
  font-family: inherit;
  color: #1f2937;
}

.field-input:focus {
  border-color: #667eea;
}

.icon-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.icon-preview {
  width: 60px;
  height: 60px;
  border-radius: 12px;
  background: #f3f0ff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
}

.icon-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.icon-preview .emoji {
  font-size: 30px;
  line-height: 1;
}

.icon-actions {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.upload-btn {
  display: inline-block;
  border: 1px solid #d8d2f5;
  background: #f7f5ff;
  color: #667eea;
  border-radius: 9px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  text-align: center;
}

.upload-btn:hover {
  background: #efeaff;
}

.icon-tip {
  font-size: 11.5px;
  color: #9ca3af;
}

.modal-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
}

.spacer {
  flex: 1;
}

.btn {
  border: 1px solid #e5e7eb;
  background: white;
  color: #4b5563;
  border-radius: 9px;
  padding: 7px 16px;
  font-size: 13.5px;
  cursor: pointer;
  transition: all 0.15s;
}

.btn:hover {
  border-color: #c7b8f7;
  color: #667eea;
}

.btn.ghost {
  border-color: transparent;
  color: #9ca3af;
}

.btn.primary {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-color: transparent;
  color: white;
}

.btn.primary:hover {
  opacity: 0.92;
  color: white;
}

@media (max-width: 640px) {
  .home {
    padding: 20px 14px;
  }
  .grid {
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 12px;
  }
  .card-icon {
    width: 60px;
    height: 60px;
  }
  .card-icon .emoji {
    font-size: 30px;
  }
}
</style>
