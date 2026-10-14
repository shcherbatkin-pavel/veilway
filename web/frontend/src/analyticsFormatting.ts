import type { ProfileMode } from "./api";

export const modeNames: Record<ProfileMode, string> = { "aws-direct": "AWS напрямую", "yc-direct": "Yandex напрямую", "yc-aws-multihop": "Yandex → AWS" };
export const sourceNames = { unconfigured: "Сбор не настроен", unavailable: "Сбор недоступен", stale: "Данные устарели", fresh: "Свежие данные" };
export const number = (value: number | null) => value === null ? "—" : value.toLocaleString("ru-RU");
export const date = (value: string | null) => value ? new Date(value).toLocaleString("ru-RU") : "Не наблюдалось";
export function bytes(value: number) {
  const units = ["Б", "КиБ", "МиБ", "ГиБ", "ТиБ", "ПиБ"];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}
export function duration(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${Math.floor(value)} с`;
  if (value < 3600) return `${Math.floor(value / 60)} мин`;
  return `${Math.floor(value / 3600)} ч ${Math.floor(value % 3600 / 60)} мин`;
}
