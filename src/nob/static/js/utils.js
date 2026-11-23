/**
 * Shared pure formatting, validation, timeframe, icon, and IRST (UTC+03:30) helpers.
 * Honors ADR-010 (fixed IRST UTC+03:30, no DST drift) and ADR-012 (100% offline).
 */

export const IRST_OFFSET_MS = (3 * 60 + 30) * 60 * 1000;

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const SYMBOL_RE = /^[A-Z0-9_-]{2,24}$/;

export function getTehranNow(nowMs = Date.now()) {
  const d = new Date(nowMs + IRST_OFFSET_MS);
  return {
    gy: d.getUTCFullYear(),
    gm: d.getUTCMonth() + 1,
    gd: d.getUTCDate(),
    hh: String(d.getUTCHours()).padStart(2, '0'),
    mm: String(d.getUTCMinutes()).padStart(2, '0'),
    ss: String(d.getUTCSeconds()).padStart(2, '0'),
  };
}

export function getTehranTimeStr(nowMs = Date.now()) {
  const { hh, mm, ss } = getTehranNow(nowMs);
  return `${hh}:${mm}:${ss}`;
}

export function formatDateIsoTehran(dateOrMs = Date.now()) {
  const ms = dateOrMs instanceof Date ? dateOrMs.getTime() : Number(dateOrMs);
  const { gy, gm, gd } = getTehranNow(ms);
  return `${gy}-${String(gm).padStart(2, '0')}-${String(gd).padStart(2, '0')}`;
}

export function getTehranDateOffsetIso(daysDelta = 0, nowMs = Date.now()) {
  return formatDateIsoTehran(nowMs + daysDelta * 86400 * 1000);
}

export function getTehranYearStartIso(nowMs = Date.now()) {
  const { gy } = getTehranNow(nowMs);
  return `${gy}-01-01`;
}

export function isValidIsoDate(s) {
  if (!s || typeof s !== 'string' || !ISO_DATE_RE.test(s)) return false;
  const [y, m, d] = s.split('-').map(Number);
  if (m < 1 || m > 12 || d < 1 || d > 31) return false;
  const dt = new Date(Date.UTC(y, m - 1, d));
  return (
    dt.getUTCFullYear() === y &&
    dt.getUTCMonth() + 1 === m &&
    dt.getUTCDate() === d
  );
}

export function isValidSymbol(s) {
  if (!s || typeof s !== 'string') return false;
  return SYMBOL_RE.test(s.trim().toUpperCase());
}

export function getResolutionForTimeframe(tf) {
  const norm = String(tf || '').trim().toUpperCase();
  switch (norm) {
    case '1M': return '1';
    case '5M': return '5';
    case '15M': return '15';
    case '30M': return '30';
    case '1H':
    case '60': return '60';
    case '4H':
    case '240': return '240';
    case '1D':
    case 'D': return 'D';
    case '1W':
    case 'W': return '1W';
    default: return 'D';
  }
}

export function normalizeWsResolution(res) {
  const r = String(res || '').trim().toUpperCase();
  if (r === '1W' || r === 'W') return null;
  if (r === '1D' || r === 'D') return 'D';
  if (r === '1H') return '60';
  if (r === '4H') return '240';
  if (r === '15M') return '15';
  if (r === '30M') return '30';
  if (r === '5M') return '5';
  if (r === '1M') return '1';
  return r;
}

export function formatCurrency(val, decimals = 2) {
  const num = parseFloat(val);
  if (isNaN(num)) return '0.00';
  return Math.abs(num).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatSignedCurrency(val, decimals = 2) {
  const num = parseFloat(val);
  if (isNaN(num)) return '$0.00';
  const sign = num > 0 ? '+' : (num < 0 ? '-' : '');
  const abs = Math.abs(num).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return `${sign}$${abs}`;
}

export function formatSignedPercent(val, decimals = 1) {
  const num = parseFloat(val);
  if (isNaN(num)) return '0.0%';
  const sign = num > 0 ? '+' : (num < 0 ? '-' : '');
  const abs = Math.abs(num).toFixed(decimals);
  return `${sign}${abs}%`;
}

export function formatNumber(val) {
  const num = parseFloat(val);
  if (isNaN(num)) return val;
  return num.toLocaleString('en-US', { maximumFractionDigits: 4 });
}

export function formatAmount(val, maxDecimals = 8) {
  const num = parseFloat(val);
  if (isNaN(num)) return val;
  return num.toLocaleString('en-US', { maximumFractionDigits: maxDecimals });
}

export function formatNetAmount(tr) {
  if (!tr || tr.type !== 'buy') return '—';
  if (tr.net_amount !== undefined && tr.net_amount !== null && !isNaN(parseFloat(tr.net_amount))) {
    return formatAmount(tr.net_amount);
  }
  const amt = parseFloat(tr.amount) || 0;
  const fee = parseFloat(tr.fee) || 0;
  return formatAmount(Math.max(0, amt - fee));
}

export function isTomanMarket(market) {
  if (!market) return false;
  const m = String(market).toUpperCase();
  return m.includes('RLS') || m.includes('IRT') || m.includes('IRR');
}

export function formatMarket(market) {
  if (!market) return '-';
  return String(market).toUpperCase().replace('RLS', 'IRT').replace('IRR', 'IRT');
}

export function formatToman(val) {
  const num = parseFloat(val);
  if (isNaN(num)) return '0';
  return Math.round(num).toLocaleString('en-US');
}

export function formatTomanLabel(numRlsOrIrt, isAlreadyIrt, lang = 'en') {
  const toman = isAlreadyIrt ? Math.round(numRlsOrIrt) : Math.round(numRlsOrIrt / 10);
  return toman.toLocaleString('en-US') + ' ' + (lang === 'fa' ? 'تومان' : 'IRT');
}

export function formatPositionPrice(val, dstCurrency, lang = 'en') {
  const num = parseFloat(val);
  if (isNaN(num) || num === 0) return '-';
  const dst = String(dstCurrency || '').toUpperCase();
  if (dst === 'RLS' || dst === 'IRR' || dst === 'IRT') {
    return formatTomanLabel(num, dst === 'IRT', lang);
  }
  const dec = num >= 1 ? 2 : 4;
  return '$' + num.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: 4 });
}

export function formatPositionPnl(val, dstCurrency, lang = 'en') {
  const num = parseFloat(val);
  if (isNaN(num)) return '-';
  const dst = String(dstCurrency || '').toUpperCase();
  const sign = num > 0 ? '+' : (num < 0 ? '-' : '');
  const absNum = Math.abs(num);
  if (dst === 'RLS' || dst === 'IRR' || dst === 'IRT') {
    return sign + formatTomanLabel(absNum, dst === 'IRT', lang);
  }
  return sign + absNum.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) + ' USDT';
}

export function formatPositionCollateral(val, srcCurrency, dstCurrency, lang = 'en') {
  const num = parseFloat(val);
  if (isNaN(num) || num === 0) return '-';
  const dst = String(dstCurrency || '').toUpperCase();
  if (dst === 'RLS' || dst === 'IRR') {
    return formatTomanLabel(num, false, lang);
  }
  return formatAmount(num);
}

export function formatPositionFee(val, dstCurrency, lang = 'en') {
  const num = parseFloat(val);
  if (isNaN(num) || num === 0) return '0';
  const dst = String(dstCurrency || '').toUpperCase();
  if (dst === 'RLS' || dst === 'IRR' || dst === 'IRT') {
    return formatTomanLabel(num, dst === 'IRT', lang);
  }
  const dec = num >= 1 ? 2 : (num >= 0.01 ? 3 : 4);
  return '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: dec });
}

export function formatTradePrice(price, market, lang = 'en') {
  const num = parseFloat(price);
  if (isNaN(num)) return price;
  if (isTomanMarket(market)) {
    return formatTomanLabel(num, false, lang);
  }
  const dec = num >= 1 ? 2 : 4;
  return '$' + num.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: 4 });
}

export function formatTradeTotal(total, market, lang = 'en') {
  const num = parseFloat(total);
  if (isNaN(num)) return total;
  if (isTomanMarket(market)) {
    return formatTomanLabel(num, false, lang);
  }
  return '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

export function formatTradeFee(fee, market, lang = 'en') {
  const num = parseFloat(fee);
  if (isNaN(num)) return fee;
  if (isTomanMarket(market)) {
    return formatTomanLabel(num, false, lang);
  }
  return formatAmount(num);
}

export function resolveAssetIcon(symbol, manifest = {}) {
  if (!symbol) return '/static/icons/generic.svg';
  const s = String(symbol).toLowerCase();
  if (manifest && manifest[s]) return manifest[s];
  if (s === 'matic') return '/static/icons/pol.svg';
  if (s === 'ton') return '/static/icons/gram.png';
  if (s === 'rls' || s === 'irr') return '/static/icons/irt.svg';
  if (manifest && Object.keys(manifest).length > 0) {
    return '/static/icons/generic.svg';
  }
  return `/static/icons/${s}.svg`;
}

export function resolveMarketBaseIcon(market, manifest = {}) {
  if (!market) return '/static/icons/generic.svg';
  const base = String(market).split('-')[0].toLowerCase();
  return resolveAssetIcon(base, manifest);
}

export function handleImgError(e) {
  if (!e || !e.target) return;
  e.target.onerror = null;
  e.target.src = '/static/icons/generic.svg';
}
