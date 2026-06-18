#!/usr/bin/env python
# coding: utf-8

# In[1]:


import pandas as pd
import numpy as np
import os
import json


# In[2]:


model_dir = './models_output_json'


# In[3]:


def load_data(trace_log_dir):
  trace_log = []
  folder = trace_log_dir
  # the list of the json files
  files = os.listdir(folder)
#!/usr/bin/env python
# coding: utf-8

import datetime
import json
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import yfinance as yfin
from pandas_datareader import data as pdr

from util import convert, get_currency_pair


model_dir = './models_output_json'


def load_data(trace_log_dir):
    trace_log = []
    folder = trace_log_dir
    files = os.listdir(folder)
    files.sort()

    for file in files:
        date = file.split('.')[0].split('_')[-1]
        date = '-'.join([date[:4], date[4:6], date[6:]])
        with open(os.path.join(folder, file)) as f:
            data = json.load(f)

        if '_' in data[0]["id"]:
            combined_weights = {}
            for stock in data:
                stock_id = stock["id"].split('_')[0]
                weight = stock["weight"]
                if stock_id in combined_weights:
                    combined_weights[stock_id] += weight
                else:
                    combined_weights[stock_id] = weight
            data = [{"id": stock_id, "weight": weight} for stock_id, weight in combined_weights.items()]

        trace_log.append(
            {
                "date": date,
                "weights": data,
            }
        )
    return trace_log


dirs = os.listdir(model_dir)
dirs = [d for d in dirs if os.path.isdir(os.path.join(model_dir, d))]
dirs.sort()

all_model_logs = {}
for dir in dirs:
    trace_log_dir = os.path.join(model_dir, dir)
    trace_log = load_data(trace_log_dir)
    all_model_logs[dir] = trace_log

unique_ids = set()
for model, trace_log in all_model_logs.items():
    for entry in trace_log:
        for stock in entry["weights"]:
            if stock["id"] == "__CASH__":
                continue
            unique_ids.add(stock["id"])

unique_ids.add('^GSPC')

stocks_data = None


def reload():
    stocks_data = yfin.download(unique_ids, start='2024-05-14', auto_adjust=False, end=None)['Adj Close']
    stocks_data.to_csv('stocks_data.csv')
    return stocks_data


stocks_data = reload()


for stock_name in list(unique_ids):
    stock_suffix = '.' + stock_name.split('.')[-1]

    exchange_name, needs_inversion, exchange_name_yahoo = get_currency_pair(stock_suffix, 'USD')
    if exchange_name is not None:
        print(f'Converting {stock_name} to USD')
        df = stocks_data[[stock_name]]
        if len(df) == 0:
            print(f'No data for {stock_name}')
            continue
        df = df.rename(columns={stock_name: 'Adj Close'})
        df['Volume'] = 0
        df = df.sort_index(ascending=True)
        df = convert(df, exchange_name, needs_inversion, exchange_name_yahoo)
        stocks_data[stock_name] = df['Adj Close']


duplicates = stocks_data.index.duplicated()
print(stocks_data.index[duplicates])

stocks_data = stocks_data.sort_index()
all_dates = pd.date_range(start=stocks_data.index.min(), end=stocks_data.index.max(), freq='D')
stocks_data = stocks_data.reindex(all_dates, method='ffill')

stocks_data.fillna(method='ffill', inplace=True)
sp500_data = stocks_data['^GSPC']

stocks_data.drop(columns=['^GSPC'], inplace=True)
unique_ids.remove('^GSPC')

sp500_log_return = pd.DataFrame(np.log(sp500_data)).diff()[1:]
log_return = pd.DataFrame(np.log(stocks_data)).diff()[1:]

log_return = log_return.fillna(0)

start_date = pd.to_datetime(log_return.index[0])


def update_weight(weights_attr, unique_ids):
    weights = np.zeros(len(unique_ids))
    for asset in weights_attr:
        asset_id = asset['id']
        if asset_id not in unique_ids:
            continue
        asset_weight = asset['weight']
        if 'operation' in asset:
            direction = asset['operation']
            if direction == 'short' and asset_weight > 0:
                asset_weight = -asset_weight
        asset_index = sorted(unique_ids).index(asset_id)
        weights[asset_index] = asset_weight
    return weights


model_asset_hists = {k: [1] for k in all_model_logs.keys()}
model_cur_weights = {k: np.zeros(len(unique_ids)) for k in all_model_logs.keys()}
model_daily_return_details = {k: [] for k in all_model_logs.keys()}
for date in log_return.index:
    date_str = str(date.date())
    for model, trace_log in all_model_logs.items():
        if pd.to_datetime(trace_log[0]['date']) > date:
            daily_log_return = sp500_log_return.loc[date_str].values[0]
            model_daily_return = daily_log_return
            cur_asset = model_asset_hists[model][-1] * np.exp(model_daily_return)
            model_asset_hists[model].append(cur_asset)
        else:
            weights = model_cur_weights[model]
            for entry in trace_log:
                if entry['date'] == date_str:
                    weights_attr = entry['weights']
                    weights = update_weight(weights_attr, unique_ids)
                    model_cur_weights[model] = weights

            daily_log_return = log_return.loc[date_str].values
            model_daily_return = np.dot(weights, daily_log_return)
            cur_asset = model_asset_hists[model][-1] * np.exp(model_daily_return)
            model_asset_hists[model].append(cur_asset)

            daily_return_details = weights * daily_log_return
            model_daily_return_details[model].append(daily_return_details)


def show_model_return_details(model_name, unique_ids, past_days=5):
    if model_name not in model_daily_return_details:
        print(f'Model {model_name} not found.')
        return

    details = model_daily_return_details[model_name]
    recent_details = details[-past_days:]
    avg_return = np.mean(recent_details, axis=0)
    return_df = pd.DataFrame({
        'Ticker': sorted(unique_ids),
        'Average Return': avg_return,
    })
    return_df = return_df.sort_values(by='Average Return', ascending=False)
    top5 = return_df.head(5)
    bottom5 = return_df.tail(5)
    result = pd.concat([top5, bottom5]).reset_index(drop=True)
    return result


combined_contrib = show_model_return_details('NEAT_62', unique_ids, past_days=5)
df = combined_contrib.set_index('Ticker')
colors = ['green' if x >= 0 else 'red' for x in df['Average Return']]

fig, ax = plt.subplots(figsize=(12, 6))
df['Average Return'].plot(
    kind='bar',
    color=colors,
    ax=ax,
    edgecolor='black',
)

ax.axhline(0, color='gray', linewidth=0.8)
ax.set_title('Top 5 and Bottom 5 Average Returns')
ax.set_ylabel('Average Return')
ax.set_xlabel('Ticker')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.show()


model_asset_hists['SP500'] = [1]
for date in sp500_log_return.index:
    date_str = str(date.date())
    daily_log_return = sp500_log_return.loc[date_str].values[0]
    model_daily_return = daily_log_return
    cur_asset = model_asset_hists['SP500'][-1] * np.exp(model_daily_return)
    model_asset_hists['SP500'].append(cur_asset)


def add_vertical_line(fig, date, annotation):
    fig.add_shape(
        dict(
            type="line",
            x0=date,
            x1=date,
            y0=0,
            y1=0.9,
            yref="paper",
            line=dict(color="blue", width=1),
        )
    )

    fig.add_annotation(
        dict(
            x=date,
            y=0.9,
            yref="paper",
            text=annotation,
            showarrow=True,
            arrowhead=2,
            ax=0,
            ay=-40,
            font=dict(size=10),
        )
    )


fig = go.Figure()
date_list = log_return.index.tolist()
date_list.insert(0, pd.Timestamp('2024-05-14'))

for model, asset_hist in model_asset_hists.items():
    fig.add_trace(go.Scatter(x=date_list, y=asset_hist, mode='lines', name=model))

fig.update_layout(title='Asset Value Over Time', xaxis_title='Date', yaxis_title='Asset Value')

add_vertical_line(fig, '2024-07-20', 'global selectKBest')
add_vertical_line(fig, '2024-10-10', 'multi-horizon')
add_vertical_line(fig, '2024-11-30', 'Short models')
add_vertical_line(fig, '2025-10-05', 'v2025!')
fig.write_image("output_chart.png", width=1200, height=800)






# In[ ]:


