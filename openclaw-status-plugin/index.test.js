import test from "node:test";
import assert from "node:assert/strict";
import { formatQuota, isTargetStatus } from "./index.js";

test("only the configured Telegram session's status is modified", () => {
  const event = { channel: "telegram", payload: { text: "🦞 OpenClaw 2026.9.9" } };
  const allowed = ["agent:main:telegram:xiaomai:direct:123"];
  assert.equal(isTargetStatus(event, { sessionKey: allowed[0] }, allowed), true);
  assert.equal(isTargetStatus(event, { sessionKey: "agent:main:telegram:xiaomai:direct:456" }, allowed), false);
  assert.equal(isTargetStatus({ ...event, channel: "discord" }, { sessionKey: allowed[0] }, allowed), false);
});

test("quota formatter does not fabricate a weekly window", () => {
  const text = formatQuota({ models: [{ model: "gemini-3.8-flash-low", remainingPercent: 88.57,
    usedPercent: 11.43, resetTime: "2026-10-27T05:02:14Z" }] }, "gemini-3.8-flash-low");
  assert.match(text, /剩余 88\.6%/);
  assert.match(text, /10\/27 13:02 重置/);
  assert.match(text, /独立周额度：Hub 未提供/);
});

test("quota formatter uses rich formattedLine when available", () => {
  const formattedLine = "💳 Quota:\n  • Gemini: 5h额度 92.1% (48m) · 周额度 78.3% (6d 2h)\n  • Claude/GPT: 5h额度 100% · 周额度 20.7% (4d 13h)";
  const text = formatQuota({ formattedLine }, "gemini-3.8-flash-low");
  assert.equal(text, formattedLine);
});
