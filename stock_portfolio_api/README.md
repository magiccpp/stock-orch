# Stock AI Portfolio API

A Flask REST API that reads model portfolio JSON files from:

```text
./models_output_json/<model_name>/
```

Portfolio filenames must end with a date in this format:

```text
<any-prefix>_YYYYMMDD.json
```

For example:

```text
portfolio_20260613.json
portfolio_20260614.json
portfolio_20260615.json
```

## Project layout

```text
stock_portfolio_api/
├── app.py
├── requirements.txt
└── models_output_json/
    ├── momentum_model/
    │   ├── portfolio_20260614.json
    │   └── portfolio_20260615.json
    └── value_model/
        └── portfolio_20260615.json
```

## Install and run

```bash
cd stock_portfolio_api

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

The API listens on:

```text
http://localhost:5000
```

Swagger UI:

```text
http://localhost:5000/apidocs/
```

## API endpoints

### List models

```bash
curl http://localhost:5000/api/models
```

### Latest portfolio

```bash
curl http://localhost:5000/api/models/momentum_model/portfolio/latest
```

### All historical portfolios

```bash
curl http://localhost:5000/api/models/momentum_model/portfolios
```

### Health check

```bash
curl http://localhost:5000/health
```

## Configure another data directory

The default data directory is `models_output_json` beside `app.py`.
Override it with an absolute or relative path:

```bash
export MODELS_OUTPUT_DIR=/data/models_output_json
python app.py
```

## Historical response format

The history endpoint preserves each source JSON document instead of assuming
that every portfolio file has the same internal schema:

```json
{
  "model_name": "momentum_model",
  "count": 2,
  "portfolios": [
    {
      "portfolio_date": "2026-06-14",
      "filename": "portfolio_20260614.json",
      "portfolio": {
        "stocks": [
          {"id": "AAA", "weight": 0.5},
          {"id": "BBB", "weight": 0.5}
        ]
      }
    }
  ]
}

```

```
cd ..
docker run -v models_output_json:/app/models_output_json -e MODELS_OUTPUT_DIR="/app/models_output_json" stock_portfolio_api:latest
```
docker run -v ./models_output_json:/app/models_output_json  -v ./stocks_data.csv:/app/stocks_data.csv -e STOCKS_DATA_PATH="/app/stocks_data.csv" -e MODELS_OUTPUT_DIR="/app/models_output_json" -p 5001:5001 stock_portfolio_api:v0.0.1