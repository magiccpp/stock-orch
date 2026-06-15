# Stock AI Performance Dashboard

A React/Vite frontend for the supplied Flask Stock AI Portfolio API.

## Features

- Left model list with multi-select chart comparison.
- Top-right cumulative-return line chart.
- Bottom-right portfolio holdings viewer.
- Click any model row to inspect that model without changing chart selection.
- Previous/next controls for historical portfolio snapshots.
- Responsive dark UI and explicit loading/error/empty states.

## Run

```bash
npm install
npm run dev
```

Run the Flask API on `http://localhost:5001`. Vite proxies `/api` and `/health` to that service.

For a separately deployed API:

```bash
cp .env.example .env
# edit VITE_API_BASE_URL
npm run build
```

## Backend endpoints used

- `GET /api/models`
- `GET /api/models/:modelName/portfolios`

The supplied `latest` endpoint is compatible but is not needed because the historical response already includes the newest snapshot.

## Portfolio JSON formats

Holdings are recognized from any of these structures:

```json
[{ "id": "AAPL", "weight": 0.2 }]
```

or an object with an array under `holdings`, `positions`, `stocks`, or `portfolio`. Asset names may use `id`, `symbol`, `ticker`, or `asset`; weights may use `weight`, `allocation`, or `percentage`.

## Return data requirement

The supplied backend has no dedicated performance endpoint. The chart therefore extracts one return value from each dated portfolio file. Supported fields include:

- `cumulative_return`
- `total_return`
- `return`
- `performance.return`
- `performance.cumulative_return`
- `metrics.return`

Values may be decimals (`0.12`) or percentage strings (`"12%"`). If portfolio files contain holdings only, actual historical returns cannot be inferred safely. In that case, add a return field to each snapshot or add a dedicated performance endpoint and update `src/lib/api.js` and `src/lib/normalizers.js`.
