#!/bin/bash
# Path to your conda initialization script
source /home/ken/anaconda3/etc/profile.d/conda.sh
# Activate the desired conda environment
conda activate stock
cd /home/ken/git/orch
python3 compare_visual_new.py
rsync -av --ignore-existing ./models_output_json/* vpn:/home/ken/git/stock-orch/models_output_json/
rsync -av --inplace stocks_data.csv vpn:/home/ken/git/stock-orch/stocks_data.csv
