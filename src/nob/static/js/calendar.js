/**
 * Shared Jalali / Gregorian calendar constants and label helpers.
 * 100% offline-compatible native ES module.
 */

export const J_MONTHS = [
  'فروردین',
  'اردیبهشت',
  'خرداد',
  'تیر',
  'مرداد',
  'شهریور',
  'مهر',
  'آبان',
  'آذر',
  'دی',
  'بهمن',
  'اسفند',
];

export const WEEK_DAYS_FA = [
  'یکشنبه',
  'دوشنبه',
  'سه‌شنبه',
  'چهارشنبه',
  'پنج‌شنبه',
  'جمعه',
  'شنبه',
];

export function formatEquityAxisLabel(dateStr, isFa, jalaali) {
  const parts = String(dateStr || '').split('-').map(Number);
  if (parts.length === 3) {
    const [gy, gm, gd] = parts;
    if (isFa && jalaali) {
      const j = jalaali.toJalaali(gy, gm, gd);
      return `${j.jd} ${J_MONTHS[j.jm - 1]}`;
    }
    const dt = new Date(Date.UTC(gy, gm - 1, gd));
    return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  }
  return dateStr;
}

export function formatSelectedDayTitle(dateKey, lang, jalaali) {
  const parts = String(dateKey || '').split('-').map(Number);
  if (parts.length !== 3) return dateKey;
  const [gy, gm, gd] = parts;
  const dateObj = new Date(Date.UTC(gy, gm - 1, gd));
  if (lang === 'fa' && jalaali) {
    const j = jalaali.toJalaali(gy, gm, gd);
    const wName = WEEK_DAYS_FA[dateObj.getUTCDay()] || '';
    return `${wName}، ${j.jd} ${J_MONTHS[j.jm - 1]} ${j.jy} (${dateKey})`;
  }
  return dateObj.toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone: 'UTC',
  });
}
