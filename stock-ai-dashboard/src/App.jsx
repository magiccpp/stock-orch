import { useCallback, useEffect, useMemo, useState } from 'react';
import ModelSidebar from './components/ModelSidebar.jsx';
import PerformanceChart from './components/PerformanceChart.jsx';
import PortfolioPanel from './components/PortfolioPanel.jsx';
import { stockApi } from './lib/api.js';
import { buildPerformanceSeries, mergePerformanceSeries } from './lib/normalizers.js';

export default function App() {
  const [models, setModels] = useState([]);
  const [selectedModels, setSelectedModels] = useState([]);
  const [focusedModel, setFocusedModel] = useState('');
  const [histories, setHistories] = useState({});
  const [portfolioIndex, setPortfolioIndex] = useState({});
  const [loadingModels, setLoadingModels] = useState(true);
  const [loadingData, setLoadingData] = useState(false);
  const [error, setError] = useState('');

  const loadModels = useCallback(async () => {
    const controller = new AbortController();
    setLoadingModels(true);
    setError('');
    try {
      const payload = await stockApi.getModels(controller.signal);
      const nextModels = payload.models || [];
      setModels(nextModels);
      setSelectedModels((current) => current.length ? current.filter((m) => nextModels.includes(m)) : nextModels.slice(0, Math.min(2, nextModels.length)));
      setFocusedModel((current) => nextModels.includes(current) ? current : nextModels[0] || '');
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingModels(false);
    }
    return () => controller.abort();
  }, []);

  useEffect(() => { loadModels(); }, [loadModels]);

  useEffect(() => {
    const missing = selectedModels.filter((model) => !histories[model]);
    if (!missing.length) return;
    const controller = new AbortController();
    setLoadingData(true);
    setError('');
    Promise.all(missing.map(async (model) => [model, await stockApi.getPortfolios(model, controller.signal)]))
      .then((results) => {
        const validResults = results.filter(([model, history]) => {
          if (!history || !Array.isArray(history.portfolios)) {
            console.error(`Invalid portfolio history for ${model}:`, history);
            return false;
          }

          return true;
        });

        setHistories((current) => {
          const next = { ...current };

          for (const [model, history] of validResults) {
            next[model] = history;
          }

          return next;
        });

        setPortfolioIndex((current) => {
          const next = { ...current };

          for (const [model, history] of validResults) {
            if (next[model] == null) {
              next[model] = Math.max(0, history.portfolios.length - 1);
            }
          }

          return next;
        });
      })
      .catch((e) => { if (e.name !== 'AbortError') setError(e.message); })
      .finally(() => setLoadingData(false));
    return () => controller.abort();
  }, [selectedModels, histories]);

  useEffect(() => {
    if (focusedModel && !histories[focusedModel]) {
      const controller = new AbortController();
      setLoadingData(true);
      stockApi.getPortfolios(focusedModel, controller.signal)
        .then((history) => {
          setHistories((current) => ({ ...current, [focusedModel]: history }));
          setPortfolioIndex((current) => ({ ...current, [focusedModel]: Math.max(0, (history.portfolios?.length || 1) - 1) }));
        })
        .catch((e) => { if (e.name !== 'AbortError') setError(e.message); })
        .finally(() => setLoadingData(false));
      return () => controller.abort();
    }
  }, [focusedModel, histories]);

  const seriesByModel = useMemo(() => Object.fromEntries(selectedModels.map((model) => [model, buildPerformanceSeries(model, histories[model])])), [selectedModels, histories]);
  const chartData = useMemo(() => mergePerformanceSeries(seriesByModel), [seriesByModel]);

  const displayModel = focusedModel || selectedModels[0] || '';
  const displayHistory = histories[displayModel];
  const displayIndex = portfolioIndex[displayModel] ?? Math.max(0, (displayHistory?.portfolios?.length || 1) - 1);

  function toggleModel(model) {
    setSelectedModels((current) => current.includes(model) ? current.filter((item) => item !== model) : [...current, model]);
    setFocusedModel(model);
  }

  return (
    <main className="app-shell">
      <ModelSidebar models={models} selected={selectedModels} focusedModel={displayModel} loading={loadingModels} onToggle={toggleModel} onFocus={setFocusedModel} onRefresh={loadModels} />
      <div className="workspace">
        {error && <div className="error-banner"><strong>API error:</strong> {error}</div>}
        <PerformanceChart data={chartData} models={selectedModels} loading={loadingData} />
        <PortfolioPanel modelName={displayModel} history={displayHistory} index={displayIndex} loading={loadingData} onIndexChange={(index) => setPortfolioIndex((current) => ({ ...current, [displayModel]: index }))} />
      </div>
    </main>
  );
}
