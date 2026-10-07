import { Profile, ProfileJob, ProfileMode, ProfileStatus } from "./api";
export const modes: Record<ProfileMode, string> = { "yc-direct": "Yandex Direct", "aws-direct": "AWS Direct", "yc-aws-multihop": "Yandex → AWS" };
export const statuses: Record<ProfileStatus, string> = { issuing: "Выпускается", active: "Активен", expired: "Истёк", revoking: "Отзыв применяется", revoked: "Отозван", failed: "Ошибка выпуска" };
export function effectiveStatus(profile: Profile, now = Date.now()): ProfileStatus {
  return profile.status === "active" && new Date(profile.expires_at).getTime() <= now ? "expired" : profile.status;
}
export function jobError(job?: ProfileJob, admin = true): string {
  if (!job) return "";
  if (!admin && ["failed", "needs_review"].includes(job.status)) return "Не удалось выпустить профиль. Обратитесь к администратору.";
  if (job.error_code === "pki_expiry_rejected") return "Срок профиля превышает срок действия центра сертификации. Создайте профиль с меньшим сроком.";
  if (job.status === "needs_review") return "Требуется проверка администратором. Повторный выпуск автоматически не выполняется.";
  if (job.status === "failed") return "Не удалось выпустить профиль. Обратитесь к администратору.";
  if (job.error_code) return "Сервис выпуска профилей временно недоступен. Операция будет повторена автоматически.";
  return "";
}
export function dateLabel(value: string) {
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}
