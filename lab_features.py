"""Structured-window adaptation of steel-scrap-price-prediction (MIT).

Copyright (c) 2026 hyunwoomoon1990; see third_party/scrap_prediction/LICENSE.
Reference: 4_baselines/classical_baselines.py::struct_window (past-window
flattening), 1_data/prep_data.py::build_samples (horizon direction labels).
Our implementation uses exact original observation dates, six backward-only
observations, no +/- date lookup, no bfill and explicit three-way labels.
"""
import numpy as np

LOOKBACK=6
FEATURES=[f'price_window_{i}' for i in range(LOOKBACK)]+['return_1','return_2','return_4','volatility_5','mean_gap_6']


def struct_window(prices,index,past=LOOKBACK):
    if index<past-1:raise ValueError('歷史視窗不足')
    window=np.asarray(prices[index-past+1:index+1],dtype=float)
    if not np.isfinite(window).all() or (window<=0).any():raise ValueError('價格必須為正有限數值')
    # Flatten the backward structured window, scale using only its current price.
    returns=np.diff(window)/window[:-1]
    return [*(window/window[-1]-1).tolist(),*(window[-1]/window[-1-lag]-1 for lag in (1,2,4)),
            float(np.std(returns)),float(window.mean()/window[-1]-1)]


def direction(change,threshold=.0025):
    return 2 if change>threshold else 0 if change < -threshold else 1
