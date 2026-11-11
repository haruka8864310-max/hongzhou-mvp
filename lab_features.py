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


def available_points(series,cutoff):
    """Original dated official releases may support PUBLICATION replay only.

    All other snapshots retain strict acquisition gating. first_seen/retrieved
    are never rewritten. Conflicting same-period releases are excluded.
    """
    import scrap_analysis as sa
    groups={}
    for p in series.get('points',[]):
        known=p['published_at'] if p.get('original_release') and p.get('published_at') else p['known_at']
        if sa.timestamp(known)<=cutoff and p['date']<=cutoff.astimezone(sa.TZ).date().isoformat():
            groups.setdefault(p['date'],[]).append(p)
    return [values[0] for _,values in sorted(groups.items()) if len({p['price'] for p in values})==1]


def cross_features(sources,cutoff):
    from datetime import date,timedelta
    result={}
    for slot,series in sources.items():
        points=available_points(series,cutoff);last=points[-1] if points else None
        age=(cutoff.astimezone(__import__('scrap_analysis').TZ).date()-date.fromisoformat(last['date'])).days if last else None
        limit={'daily':7,'monthly':95,'weekly':14}.get(series.get('frequency'),35)
        stale=last is None or age>limit
        result[slot+'_missing']=int(last is None);result[slot+'_age_days']=age;result[slot+'_stale']=int(stale)
        for lag in (3,5,10,20):
            change=last['price']/points[-1-lag]['price']-1 if not stale and len(points)>lag else None
            result[f'{slot}_return_{lag}_observations']=change
            # No monthly/ten-day/adjustment quotes relabeled as trading days.
            result[f'{slot}_return_{lag}_trading_days']=change if series.get('frequency')=='daily' else None
        returns=[b['price']/a['price']-1 for a,b in zip(points[-21:],points[-20:])] if len(points)>20 else []
        result[slot+'_volatility_20_observations']=float(np.std(returns)) if returns and not stale else None
        result[slot+'_direction']=direction(last['price']/points[-2]['price']-1)-1 if not stale and len(points)>1 else None
        for weeks in (1,2,3,4):
            previous=available_points(series,cutoff-timedelta(weeks=weeks))
            result[f'{slot}_lead_{weeks}w_change']=last['price']/previous[-1]['price']-1 if not stale and previous else None
    # Only identical scopes/units/currencies may enter an absolute-price spread.
    japan,us=sources.get('japan',{}),sources.get('us',{})
    comparable=all(japan.get(k) and japan.get(k)==us.get(k) for k in ('grade','delivery_term','currency','unit'))
    a,b=available_points(japan,cutoff),available_points(us,cutoff)
    result['comparable_overseas_scrap_spread']=a[-1]['price']-b[-1]['price'] if comparable and a and b else None
    return result
