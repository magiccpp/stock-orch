const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

async function request(path, signal) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: 'application/json',
    },
    signal,
  });

  const rawBody = await response.text();

  let payload = null;

  if (rawBody.trim()) {
    try {
      payload = JSON.parse(rawBody);
    } catch {
      throw new Error(
        `API returned invalid JSON for ${path}. ` +
        `HTTP ${response.status}, response size ${rawBody.length} bytes.`
      );
    }
  }

  if (!response.ok) {
    throw new Error(
      payload?.message ||
      `Request failed: HTTP ${response.status} ${response.statusText}`
    );
  }

  if (payload === null) {
    throw new Error(
      `API returned an empty response for ${path}. HTTP ${response.status}.`
    );
  }

  return payload;
}

export const stockApi = {
  getModels: (signal) => request('/api/models', signal),
  getLatestPortfolio: (modelName, signal) =>
    request(`/api/models/${encodeURIComponent(modelName)}/portfolio/latest`, signal),
  getPortfolios: (modelName, signal) =>
    request(`/api/models/${encodeURIComponent(modelName)}/portfolios`, signal),
};
