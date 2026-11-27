import { env } from 'node:process';

export async function sendTelegramMessage(text, {
  botToken = env.TELEGRAM_BOT_TOKEN,
  chatId = env.TELEGRAM_CHAT_ID,
} = {}) {
  if (!botToken || !chatId) {
    throw new Error('请设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID');
  }
  if (!/^\d+:[A-Za-z0-9_-]+$/.test(botToken)) {
    throw new Error('Telegram Bot Token 格式无效');
  }

  let response;
  let message;
  try {
    response = await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text, link_preview_options: { is_disabled: true } }),
      redirect: 'error',
      signal: AbortSignal.timeout(8000),
    });
    message = await response.json();
  } catch {
    // fetch 的原始错误可能包含带 Bot Token 的请求地址。
    throw new Error(response ? `Telegram 返回无效响应（HTTP ${response.status}）` : 'Telegram 连接失败或超时');
  }
  if (!response.ok || !message?.ok) {
    if (message?.description === 'Bad Request: chat not found') {
      throw new Error('Telegram 找不到聊天，请先在 Telegram 打开机器人并点击「开始」，再核对 chat ID');
    }
    throw new Error(`Telegram 拒绝了通知（HTTP ${response.status}）`);
  }
  return message.result;
}
