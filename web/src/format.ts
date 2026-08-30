import type { VocabularyItem } from "./model";

const CRC_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) {
    value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  }
  return value >>> 0;
});

export function crc32(value: string): number {
  let crc = 0xffffffff;
  for (const byte of new TextEncoder().encode(value)) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function memberColor(name: string): string {
  return `var(--member-${crc32(name) % 8})`;
}

export function minuteGroup(ts: string): string {
  return ts.slice(0, 16).replace("T", " ");
}

export function formatTime(epochSeconds: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(epochSeconds * 1000);
}

export function relativeActivity(
  silentFor: number | null,
  snapshotAt: number,
  nowMs = Date.now(),
): string {
  if (silentFor === null) return "暂无输出";
  const elapsed = Math.max(0, silentFor + (nowMs / 1000 - snapshotAt));
  if (elapsed < 10) return "刚刚活跃";
  if (elapsed < 60) return `${Math.floor(elapsed)} 秒前`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} 分前`;
  return `${Math.floor(elapsed / 3600)} 小时前`;
}

export function vocabularyItem(
  items: VocabularyItem[] | undefined,
  value: string,
): VocabularyItem {
  return items?.find((item) => (item.value ?? item.key) === value) ?? { label: value };
}
