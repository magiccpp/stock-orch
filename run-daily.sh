#!/bin/bash
# Path to your conda initialization script
source /home/ken/anaconda3/etc/profile.d/conda.sh
# Activate the desired conda environment
conda activate stock
cd /home/ken/git/orch
python3 compare_visual_new.py

# Import today's portfolio files and stock prices into Postgres (skips
# anything already imported) and recompute each model's returns, so the
# stock_portfolio_api container serves them without touching the files
# directly.
docker compose exec -T stock_portfolio_api python import_data.py

rsync -av --ignore-existing ./models_output_json/* vpn-2:/home/ken/git/stock-orch/models_output_json/
rsync -av --inplace stocks_data.csv vpn-2:/home/ken/git/stock-orch/stocks_data.csv

# Real IBKR paper returns (written by ~/git/ibkr-trader report at 07:30) for the dashboard.
rsync -av data/ibkr/ vpn-2:/home/ken/git/stock-orch/data/ibkr/
