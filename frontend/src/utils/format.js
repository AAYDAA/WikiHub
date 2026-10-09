/**
 * 展示层格式化工具。
 *
 * 刻意不依赖任何框架 —— 换 uni-app / 小程序时这个文件可原样复用。
 */

/** 时间戳 → "10/9 15:12"。 */
export const fmtTime = (ts) => {
  const d = new Date(ts)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * 把纯文本转成可安全 v-html 的 HTML。
 *
 * 必须先转义再替换换行：这里渲染的是 LLM 返回的文本，
 * 其中可能包含 < > & 等字符，直接插入 DOM 会构成 XSS 风险。
 */
export const formatContent = (text) => {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br/>')
}
