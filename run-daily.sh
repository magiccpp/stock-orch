#!/bin/bash
# Path to your conda initialization script
source /home/ken/anaconda3/etc/profile.d/conda.sh
# Activate the desired conda environment
conda activate stock
cd /home/ken/git/orch
python3 compare_visual_new.py

# Import today's portfolio files, stock prices and real IBKR returns into
# the local dashboard (http://192.168.4.51:5273, docker-compose.local.yaml)
# and recompute each model's returns.
docker compose -f docker-compose.local.yaml exec -T stock_portfolio_api python import_data.py

# Copy the same files to vpn-2, whose stock-import timer loads them into
# xiaodong.io. Changed files are re-sent (no --ignore-existing) because the
# factor generator rewrites its whole history.
rsync -av ./models_output_json/ vpn-2:/home/ken/git/stock-orch/models_output_json/
rsync -av --inplace stocks_data.csv vpn-2:/home/ken/git/stock-orch/stocks_data.csv

# Real IBKR paper returns (written by ~/git/ibkr-trader report at 07:30) for the dashboard.
rsync -av data/ibkr/ vpn-2:/home/ken/git/stock-orch/data/ibkr/
