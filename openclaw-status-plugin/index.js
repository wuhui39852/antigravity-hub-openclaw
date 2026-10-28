import { readFile } from "node:fs/promises";

const STATUS_PREFIX = "🦞 OpenClaw";
const DEFAULT_MODEL = "gemini-3.8-flash-low";

export function isTargetStatus(event, ctx, sessionKeys) {
  return event.channel === "telegram" &&
    sessionKeys.includes(ctx.sessionKey) &&
    typeof event.payload?.text === "string" &&
    event.payload.text.startsWith(STATUS_PREFIX) &&
    !event.payload.text.includes("Antigravity 配额：") &&
    !event.payload.text.includes("💳 Quota");
}

export function formatQuota(data, modelId, timeZone = "Asia/Shanghai") {
  if (data?.formattedLine) {
    return data.formattedLine;
  }
  const row = data?.models?.find((item) => item.model === modelId);
  if (!row || !Number.isFinite(row.remainingPercent)) return "Antigravity 配额：Hub 未提供该模型的剩余额度";
  const remaining = row.remainingPercent.toFixed(1);
  const used = Number.isFinite(row.usedPercent)
    ? row.usedPercent.toFixed(1) : (100 - row.remainingPercent).toFixed(1);
  let reset = "重置时间未提供";
  if (row.resetTime && !Number.isNaN(Date.parse(row.resetTime))) {
    reset = new Intl.DateTimeFormat("zh-CN", {
      timeZone, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date(row.resetTime)) + " 重置";
  }
  return `Antigravity 配额：${modelId} 剩余 ${remaining}%（已用 ${used}%）· ${reset}；独立周额度：Hub 未提供`;
}

export async function fetchQuota({ adapterUrl, keyFile, signal }) {
  const key = (await readFile(keyFile, "utf8")).trim();
  try {
    const quotaUrl = new URL("/v1/quota", adapterUrl);
    if (quotaUrl.protocol === "http:" && quotaUrl.hostname === "127.0.0.1") {
      const resp = await fetch(quotaUrl, {
        headers: { Authorization: `Bearer ${key}` }, signal,
      });
      if (resp.ok) {
        const q = await resp.json();
        if (q?.formattedLine) return q;
      }
    }
  } catch {}
  const url = new URL("/v1/usage", adapterUrl);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1") {
    throw new Error("adapterUrl must use 127.0.0.1 over HTTP");
  }
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${key}` }, signal,
  });
  if (!response.ok) throw new Error(`Hub adapter returned HTTP ${response.status}`);
  return response.json();
}

export default {
  id: "antigravity-hub-status",
  name: "Antigravity Hub Status",
  register(api) {
    const config = api.pluginConfig ?? {};
    const sessionKeys = Array.isArray(config.sessionKeys) ? config.sessionKeys : [];
    const adapterUrl = config.adapterUrl ?? "http://127.0.0.1:18800";
    const keyFile = config.keyFile ?? "";
    const model = config.model ?? DEFAULT_MODEL;
    const timeZone = config.timeZone ?? "Asia/Shanghai";
    api.on("reply_payload_sending", async (event, ctx) => {
      if (!isTargetStatus(event, ctx, sessionKeys)) return;
      if (!keyFile) return { payload: { ...event.payload, text: `${event.payload.text}\n\nAntigravity 配额：未配置密钥文件` } };
      let line;
      try {
        const data = await fetchQuota({
          adapterUrl, keyFile, signal: AbortSignal.timeout(2500),
        });
        line = formatQuota(data, model, timeZone);
      } catch {
        line = "Antigravity 配额：查询暂不可用";
      }
      return { payload: { ...event.payload, text: `${event.payload.text}\n\n${line}` } };
    });
  },
};
