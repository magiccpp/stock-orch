import { useCallback, useEffect, useMemo, useState } from 'react';
import ModelSidebar from './components/ModelSidebar.jsx';
import PerformanceChart from './components/PerformanceChart.jsx';
import PortfolioPanel from './components/PortfolioPanel.jsx';
import { stockApi } from './lib/api.js';
import { buildPerformanceSeries, mergePerformanceSeries, findClosestPortfolioIndex } from './lib/normalizers.js';

export default function App() {
  const [models, setModels] = useState([]);
  const [ibkrModels, setIbkrModels] = useState([]);
  const [selectedModels, setSelectedModels] = useState(() => {
    try { return JSON.parse(localStorage.getItem('selectedModels')) || []; } catch { return []; }
  });
  const [focusedModel, setFocusedModel] = useState(() => localStorage.getItem('focusedModel') || '');
  const [histories, setHistories] = useState({});
  const [portfolioIndex, setPortfolioIndex] = useState({});
  const [rangeSelection, setRangeSelection] = useState(null);
  const [loadingModels, setLoadingModels] = useState(true);
  const [loadingData, setLoadingData] = useState(false);
  const [error, setError] = useState('');
  const [visits, setVisits] = useState(null);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const stored = Number(localStorage.getItem('sidebarWidth'));
    return stored > 0 ? stored : 278;
  });
  const [chartHeight, setChartHeight] = useState(() => {
    const stored = Number(localStorage.getItem('chartHeight'));
    if (stored > 0) return stored;
    return Math.min(Math.max(Math.round(window.innerHeight * 0.58), 410), window.innerHeight - 260);
  });
  const [resizing, setResizing] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    stockApi.recordVisit(controller.signal)
      .then(setVisits)
      .catch((e) => { if (e.name !== 'AbortError') console.error(e); });
    return () => controller.abort();
  }, []);

  const loadModels = useCallback(async () => {
    const controller = new AbortController();
    setLoadingModels(true);
    setError('');
    try {
      const payload = await stockApi.getModels(controller.signal);
      const nextModels = payload.models || [];
      const preferredModel = nextModels.includes('human') ? 'human' : (nextModels[0] || '');
      setModels(nextModels);
      setIbkrModels(payload.ibkr_models || []);
      setSelectedModels((current) => {
        if (current.length) {
          return current.filter((m) => nextModels.includes(m));
        }

        return preferredModel ? [preferredModel] : [];
      });
      setFocusedModel((current) => nextModels.includes(current) ? current : preferredModel);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingModels(false);
    }
    return () => controller.abort();
  }, []);

  useEffect(() => { loadModels(); }, [loadModels]);

  useEffect(() => { localStorage.setItem('selectedModels', JSON.stringify(selectedModels)); }, [selectedModels]);
  useEffect(() => { localStorage.setItem('focusedModel', focusedModel); }, [focusedModel]);
  useEffect(() => { localStorage.setItem('sidebarWidth', String(sidebarWidth)); }, [sidebarWidth]);
  useEffect(() => { localStorage.setItem('chartHeight', String(chartHeight)); }, [chartHeight]);

  const startResize = useCallback((axis) => (event) => {
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const startSidebarWidth = sidebarWidth;
    const startChartHeight = chartHeight;
    setResizing(axis);

    function onMove(moveEvent) {
      if (axis === 'sidebar') {
        const next = startSidebarWidth + (moveEvent.clientX - startX);
        setSidebarWidth(Math.min(Math.max(next, 220), 480));
      } else {
        const next = startChartHeight + (moveEvent.clientY - startY);
        setChartHeight(Math.min(Math.max(next, 280), window.innerHeight - 220));
      }
    }

    function onUp() {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      setResizing(null);
    }

    document.body.style.cursor = axis === 'sidebar' ? 'col-resize' : 'row-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [sidebarWidth, chartHeight]);

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

  function handleChartDateSelect(date) {
    setRangeSelection(null);
    const portfolios = displayHistory?.portfolios;
    if (!date || !displayModel || !portfolios?.length) return;
    const closestIndex = findClosestPortfolioIndex(portfolios, date);
    setPortfolioIndex((current) => ({ ...current, [displayModel]: closestIndex }));
  }

  function handleChartRangeSelect(start, end) {
    if (!start || !end) return;
    setRangeSelection({ start, end });
  }

  return (
    <main className="app-shell" style={{ '--sidebar-width': `${sidebarWidth}px` }}>
      <ModelSidebar models={models} ibkrModels={ibkrModels} selected={selectedModels} focusedModel={displayModel} loading={loadingModels} onToggle={toggleModel} onFocus={setFocusedModel} onRefresh={loadModels} visits={visits} />
      <div
        className={`resize-handle resize-handle--vertical ${resizing === 'sidebar' ? 'active' : ''}`}
        onMouseDown={startResize('sidebar')}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
      />
      <div className="workspace" style={{ '--chart-height': `${chartHeight}px` }}>
        {error && <div className="error-banner"><strong>API error:</strong> {error}</div>}
        <PerformanceChart data={chartData} models={selectedModels} loading={loadingData} onSelectDate={handleChartDateSelect} onSelectRange={handleChartRangeSelect} selectedRange={rangeSelection} />
        <div
          className={`resize-handle resize-handle--horizontal ${resizing === 'chart' ? 'active' : ''}`}
          onMouseDown={startResize('chart')}
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize chart panel"
        />
        <PortfolioPanel
          modelName={displayModel}
          history={displayHistory}
          index={displayIndex}
          loading={loadingData}
          onIndexChange={(index) => setPortfolioIndex((current) => ({ ...current, [displayModel]: index }))}
          rangeSelection={rangeSelection}
          onClearRange={() => setRangeSelection(null)}
        />
      </div>
    </main>
  );
}
