const RETURN_PATHS = [
  ['cumulative_return'],
  ['cumulativeReturn'],
  ['total_return'],
  ['totalReturn'],
  ['return'],
  ['performance', 'cumulative_return'],
  ['performance', 'return'],
  ['metrics', 'cumulative_return'],
  ['metrics', 'return'],
];

function getPath(value, path) {
  return path.reduce((current, key) => current?.[key], value);
}

function asFiniteNumber(value) {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    const percent = trimmed.endsWith('%');
    const parsed = Number.parseFloat(trimmed.replace('%', ''));
    return Number.isFinite(parsed) ? (percent ? parsed / 100 : parsed) : null;
  }
  return Number.isFinite(value) ? value : null;
}

export function extractReturn(portfolio) {
  for (const path of RETURN_PATHS) {
    const result = asFiniteNumber(getPath(portfolio, path));
    if (result !== null) return result;
  }
  return null;
}

export function buildPerformanceSeries(modelName, history) {
  return (history?.portfolios || [])
    .map((entry) => ({
      date: entry.portfolio_date,
      model: modelName,
      value: extractReturn(entry.portfolio),
    }))
    .filter((point) => point.date && point.value !== null)
    .sort((a, b) => a.date.localeCompare(b.date));
}

export function mergePerformanceSeries(seriesByModel) {
  const rows = new Map();
  for (const [model, series] of Object.entries(seriesByModel)) {
    for (const point of series) {
      const row = rows.get(point.date) || { date: point.date };
      row[model] = point.value;
      rows.set(point.date, row);
    }
  }
  return [...rows.values()].sort((a, b) => a.date.localeCompare(b.date));
}

export function normalizeHoldings(portfolio) {
  const candidate = Array.isArray(portfolio)
    ? portfolio
    : portfolio?.holdings || portfolio?.positions || portfolio?.stocks || portfolio?.portfolio || [];

  if (!Array.isArray(candidate)) return [];

  return candidate.map((item, index) => {
    if (typeof item === 'string') {
      return { id: item, weight: null, raw: item, key: `${item}-${index}` };
    }
    const id = item?.id ?? item?.symbol ?? item?.ticker ?? item?.asset ?? `Position ${index + 1}`;
    const weight = asFiniteNumber(item?.weight ?? item?.allocation ?? item?.percentage);
    return { id: String(id), weight, raw: item, key: `${id}-${index}` };
  });
}

export function formatPercent(value) {
  return value == null ? '—' : `${(value * 100).toFixed(2)}%`;
}

export function withWeightDeltas(holdings, prevHoldings) {
  const hasPrev = Boolean(prevHoldings?.length);
  const prevWeightById = new Map((prevHoldings || []).map((h) => [h.id, h.weight]));

  return holdings.map((holding) => {
    const prevWeight = prevWeightById.has(holding.id) ? prevWeightById.get(holding.id) : null;
    const delta = holding.weight != null && prevWeight != null ? holding.weight - prevWeight : null;
    return { ...holding, prevWeight, delta, isNew: hasPrev && !prevWeightById.has(holding.id) };
  });
}

export function formatDelta(value) {
  if (value == null) return '';
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(2)}%`;
}
