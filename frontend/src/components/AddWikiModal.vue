<script setup>
/**
 * 添加 Wiki 弹窗（首页）
 *
 * 为什么拆成「解析 → 确认」两步：
 *   探测由后端做（api.php 事实判断 + AI 剥游戏名）。AI 剥名偶尔会错，
 *   所以先把结果显示出来让用户改，确认后才写 games.json —— 直接落库的话
 *   改错了还得手动去编辑后端文件。
 *
 * 为什么图标抓不到也照样能用：
 *   后端会先试真实图标，取不到就退回一个 emoji。前端本来就同时支持
 *   emoji 和图片 URL（见 GameGrid 的 isImageIcon），所以两种都能显示。
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { probeWiki, registerWiki } from '../api/index.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'added'])

const url = ref('')
const probing = ref(false)
const registering = ref(false)
const errorText = ref('')
const result = ref(null)
const form = ref({ id: '', name: '', icon: '' })

const close = () => emit('update:modelValue', false)

const reset = () => {
  url.value = ''
  result.value = null
  errorText.value = ''
  form.value = { id: '', name: '', icon: '' }
}

// 关闭即清空：否则下次打开会看到上一次的探测结果，容易误点确认
watch(() => props.modelValue, (v) => { if (!v) reset() })

const isImageIcon = (v) => typeof v === 'string' && (v.startsWith('data:') || v.startsWith('http'))

const doProbe = async () => {
  const u = url.value.trim()
  if (!u) {
    ElMessage.warning('请输入 Wiki 地址')
    return
  }
  probing.value = true
  errorText.value = ''
  result.value = null
  try {
    const res = await probeWiki(u)
    result.value = res.data
    form.value = {
      id: res.data.suggested_id,
      name: res.data.suggested_name,
      icon: res.data.suggested_icon,
    }
  } catch (e) {
    errorText.value = e.response?.data?.detail || e.message || '解析失败'
  } finally {
    probing.value = false
  }
}

const doRegister = async () => {
  if (!form.value.name.trim()) {
    ElMessage.warning('游戏名不能为空')
    return
  }
  if (!form.value.id.trim()) {
    ElMessage.warning('id 不能为空')
    return
  }
  registering.value = true
  errorText.value = ''
  try {
    const res = await registerWiki({
      id: form.value.id.trim(),
      name: form.value.name.trim(),
      icon: form.value.icon || '🎮',
      wiki_base_url: result.value.wiki_base_url,
      wiki_api_url: result.value.wiki_api_url,
    })
    // 明确告知还需建索引：只登记的话卡片会一直是置灰的「未建知识库」
    ElMessage.success(`已添加「${res.data.game.name}」，还需构建索引才能问答`)
    emit('added', res.data.game)
    close()
  } catch (e) {
    errorText.value = e.response?.data?.detail || e.message || '添加失败'
  } finally {
    registering.value = false
  }
}
</script>

<template>
  <div v-if="props.modelValue" class="modal-mask" @click.self="close">
    <div class="modal">
      <div class="modal-head">
        <span>添加 Wiki</span>
        <span class="modal-close" @click="close">✕</span>
      </div>

      <div class="modal-body">
        <label class="field-label">Wiki 地址</label>
        <div class="url-row">
          <input
            v-model="url"
            class="field-input url-input"
            placeholder="例如 isaac.huijiwiki.com"
            @keyup.enter="doProbe"
          />
          <button class="btn primary probe-btn" :disabled="probing" @click="doProbe">
            {{ probing ? '解析中…' : '解析' }}
          </button>
        </div>
        <div class="field-tip">填域名或页面地址都行，会自动识别站点信息</div>

        <div v-if="errorText" class="err-box">{{ errorText }}</div>

        <!-- 解析结果预览：确认无误再添加 -->
        <template v-if="result">
          <div class="preview">
            <div class="preview-icon">
              <img v-if="isImageIcon(form.icon)" :src="form.icon" alt="" />
              <span v-else class="emoji">{{ form.icon }}</span>
            </div>
            <div class="preview-main">
              <div class="preview-name">{{ form.name || '(未取名)' }}</div>
              <div class="preview-sub">站点名：{{ result.sitename }}</div>
            </div>
          </div>

          <div v-if="result.is_game === false" class="warn-box">
            ⚠️ AI 判断这不是游戏主题的 Wiki：{{ result.reason || '建议确认后再添加' }}
          </div>
          <div v-else-if="result.reason" class="ok-box">✅ {{ result.reason }}</div>

          <div v-if="result.icon_source === 'none'" class="field-tip">
            没能取到站点图标，已用 emoji 代替 —— 添加后可在卡片右上角 ✎ 里换成图片
          </div>

          <label class="field-label">游戏名</label>
          <input v-model="form.name" class="field-input" maxlength="20" placeholder="显示在卡片上的名字" />

          <label class="field-label">id（内部标识，会作为索引目录名）</label>
          <input v-model="form.id" class="field-input" placeholder="如 isaac" />

          <div class="meta-box">
            <div class="meta-line"><span>接口</span><code>{{ result.wiki_api_url }}</code></div>
            <div class="meta-line"><span>引擎</span><code>{{ result.generator || '未知' }}</code></div>
          </div>

          <div class="field-tip add-tip">
            添加后只会完成「登记」。要能问答，还需在 backend 下跑：
            <code>python -m src.build_index --game {{ form.id || '&lt;id&gt;' }}</code>
          </div>
        </template>
      </div>

      <div class="modal-foot">
        <span class="spacer"></span>
        <button class="btn" @click="close">取消</button>
        <button
          v-if="result"
          class="btn primary"
          :disabled="registering"
          @click="doRegister"
        >
          {{ registering ? '添加中…' : '确认添加' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 样式与 GameGrid.vue 的编辑弹窗保持一致，避免两处弹窗长得不一样 */
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
  width: 460px;
  max-width: 100%;
  max-height: 88vh;
  overflow-y: auto;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.2);
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

.field-tip {
  font-size: 11.5px;
  color: #9ca3af;
  margin: -10px 0 14px;
  line-height: 1.5;
}

.url-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.url-input {
  flex: 1;
}

.probe-btn {
  flex-shrink: 0;
  padding: 9px 18px;
}

.err-box {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #b91c1c;
  border-radius: 9px;
  padding: 9px 12px;
  font-size: 12.5px;
  line-height: 1.55;
  margin-bottom: 14px;
}

.warn-box {
  background: #fffbeb;
  border: 1px solid #fde68a;
  color: #92400e;
  border-radius: 9px;
  padding: 9px 12px;
  font-size: 12.5px;
  line-height: 1.55;
  margin-bottom: 14px;
}

.ok-box {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #15803d;
  border-radius: 9px;
  padding: 9px 12px;
  font-size: 12.5px;
  line-height: 1.55;
  margin-bottom: 14px;
}

.preview {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #faf9ff;
  border: 1px solid #ede9fe;
  border-radius: 12px;
  padding: 12px;
  margin-bottom: 14px;
}

.preview-icon {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  background: #f3f0ff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
}

.preview-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.preview-icon .emoji {
  font-size: 28px;
  line-height: 1;
}

.preview-main {
  min-width: 0;
}

.preview-name {
  font-size: 15px;
  font-weight: 600;
  color: #374151;
  word-break: break-all;
}

.preview-sub {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 3px;
  word-break: break-all;
}

.meta-box {
  background: #f9fafb;
  border: 1px solid #f0f0f0;
  border-radius: 9px;
  padding: 10px 12px;
  margin-bottom: 14px;
}

.meta-line {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: #6b7280;
  line-height: 1.7;
}

.meta-line span {
  flex-shrink: 0;
  width: 30px;
  color: #9ca3af;
}

.meta-line code {
  word-break: break-all;
  color: #4b5563;
  font-size: 11.5px;
}

.add-tip {
  margin: 0;
}

.add-tip code {
  background: #f3f4f6;
  border-radius: 4px;
  padding: 1px 5px;
  color: #4b5563;
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

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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
  .modal-body {
    padding: 14px;
  }
}
</style>
