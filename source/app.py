"""Rocket Stock Scanner — Main Dash Application."""

import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, callback, ctx
import dash_bootstrap_components as dbc

# Add rocket to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rocket.data.fetcher import fetch_ohlcv
from rocket.data.storage import load_ohlcv, save_ohlcv
from rocket.data.models import TickerInfo, Region
from rocket.data.universe_regions import REGION_META
from rocket.technical.momentum import (RSI, MACD, Stochastic, WilliamsR,
                                        ROC, CCI)
from rocket.technical.trend import (EMA9, EMA21, EMA50, EMA200,
                                     EMACrossover, ADX)
from rocket.technical.volatility import BollingerBands, ATR, DonchianChannel
from rocket.technical.volume import OBV, MFI, VWAPIndicator
from rocket.technical.advanced import IchimokuCloud, Supertrend, ParabolicSAR
from rocket.technical.signal_combiner import SignalCombiner, SignalSummary
from rocket.technical.models import Signal
from rocket.scoring.weighter import weight_scores
from rocket.scoring.filter import apply_filters
from rocket.plotting.candlestick import create_candlestick
from rocket.plotting.indicators import add_indicators_to_chart
from rocket.plotting.equity import create_equity_curve
from rocket.backtest.engine import run_backtest
from rocket.backtest.strategy import (BaseStrategy,
                                       EMACrossoverStrategy,
                                       RSIBasedStrategy)
from rocket.backtest.models import Trade
from rocket.sentiment.news import fetch_news
from rocket.sentiment.models import NewsArticle, SentimentScore

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)

# App config
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = str(BASE_DIR / "data" / "raw")
DEFAULT_REGIONS = ["usa"]

# All indicator instances (reuse from rocket.scoring.rocket_score)
INDICATORS = [
    RSI(), MACD(), Stochastic(), WilliamsR(), ROC(), CCI(),
    EMA9(), EMA21(), EMA50(), EMA200(), EMACrossover(), ADX(),
    BollingerBands(), ATR(), DonchianChannel(),
    OBV(), MFI(), VWAPIndicator(),
    IchimokuCloud(), Supertrend(), ParabolicSAR(),
]

# Create Dash app
app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="Rocket Stock Scanner",
)

# ========================
# Helpers
# ========================

def _compute_all_indicators(df: pd.DataFrame):
    """Run all indicator instances on df. Return (SignalSummary, list[IndicatorResult])."""
    results = []
    for indicator in INDICATORS:
        try:
            r = indicator.calculate(df)
            results.append(r)
        except Exception as e:
            logger.warning(f"Indicator {indicator.__class__.__name__} failed: {e}")
    combiner = SignalCombiner()
    summary = combiner.combine(results)
    return summary, results


def _indicator_values_to_dicts(results) -> List[Dict[str, Any]]:
    """Convert IndicatorResult list to dicts for add_indicators_to_chart."""
    out = []
    for r in results:
        vals = dict(r.values) if hasattr(r, 'values') else {}
        out.append({"name": r.name, "values": vals, "category": r.category.value})
    return out


# Map registry region names → legacy Region enum values. Bucket choice is
# cosmetic, not functional (no region branch in apply_filters/weight_scores,
# DESIGN §2 VERIFIED) — the map exists ONLY so Region() below cannot raise
# for a registry key. F1 (MC 10223): every REGION_META key resolves; the kept
# mappings are unchanged and the golden test pins the 35 demo scores exactly.
_REGION_MAP = {
    "sweden": "smid",
    "usa": "us",
    "china": "asia",
    "india": "asia",
    "germany": "eu",
    "uk": "eu",
    "france": "eu",
    "switzerland": "eu",
    "norway": "eu",
    "denmark": "eu",
    "finland": "eu",
    "canada": "us",
    "brazil": "us",
    "australia": "asia",
    "japan": "asia",
    "hongkong": "asia",
    "korea": "asia",
    "singapore": "asia",
    # DESIGN §3 pins the key's bucket as the US enum value ("usa" in the doc,
    # "us" here — the enum value, not the region name; "usa" would make
    # Region(...) raise, violating the design's own never-raises test).
    "international": "us",  # never scored — the loader drops it (DESIGN §4)
}


def _assert_region_map(region_map: dict, region_meta: dict) -> None:
    """Raise on any REGION_META key the map cannot resolve — LOUD at load.
    Without this, Region(<KEY>) raises per ticker and the per-ticker
    `except Exception` turns a whole region into 0 scored rows with a
    GREEN guard (F1: 3,569 tickers / 11 regions, 27.9 %)."""
    missing = sorted(set(region_meta) - set(region_map))
    if missing:
        raise RuntimeError(f"_REGION_MAP missing REGION_META keys: {missing}")


_assert_region_map(_REGION_MAP, REGION_META)


def _score_from_summary(summary, ticker: str = "", region: str = "us", sector: str = "") -> Dict:
    """Compute full RocketScore from a SignalSummary (with dummy ticker info)."""
    legacy_region = _REGION_MAP.get(region.lower(), region.upper())
    ticker_info = TickerInfo(ticker=ticker, region=Region(legacy_region))
    filter_result = apply_filters(ticker_info, 0.0)
    rocket_score = weight_scores(summary, filter_result, ticker, region, sector)
    return {
        "rocket_score": rocket_score,
        "signal_summary": summary,
        "filter_result": filter_result,
    }


def _make_gauge(value: float, title: str, color: str = "#00e676") -> html.Div:
    """Small score gauge card."""
    return html.Div([
        html.Div(f"{value:.1f}", style={
            'fontSize': 28, 'fontWeight': 'bold', 'color': color, 'textAlign': 'center'}),
        html.Div(title, style={
            'fontSize': 11, 'color': '#9e9e9e', 'textAlign': 'center', 'marginTop': -4}),
    ], style={
        'flex': 1, 'backgroundColor': '#1a1a2e', 'borderRadius': 8,
        'padding': '12px 8px', 'textAlign': 'center', 'border': '1px solid #2a2a3e'})


def _get_universe_for_regions(regions: List[str]) -> List[str]:
    """Get tickers for the given regions."""
    from rocket.data.universe import get_universe
    tickers = []
    for r in regions:
        try:
            tickers.extend(get_universe(r))
        except ValueError:
            pass
    return sorted(set(tickers))


def _get_all_region_options():
    """Return region dropdown options with labels."""
    from rocket.data.universe import REGION_LABELS
    return [{'label': REGION_LABELS.get(r, r), 'value': r}
            for r in ["sweden", "usa", "china", "india"]]


def _build_ticker_dropdown_options(regions: List[str] = None, limit: int = 2000):
    """Build ticker dropdown options from specified regions."""
    from rocket.data.universe import REGIONS
    tickers = []
    if regions:
        for r in regions:
            try:
                tickers.extend(REGIONS.get(r, []))
            except (AttributeError, TypeError):
                pass
    else:
        for t in REGIONS.values():
            tickers.extend(t)
    return [{'label': t, 'value': t} for t in sorted(set(tickers))[:limit]]


def _refresh_data(data_dir: str, regions: List[str] = None, days: int = 365):
    """Fetch OHLCV data for tickers in given regions."""
    if regions is None:
        regions = ["sweden", "usa", "china", "india"]
    counts = {"fetched": 0, "errors": 0, "skipped": 0}
    tickers = _get_universe_for_regions(regions)
    logger.info(f"Refreshing data for {len(tickers)} tickers across {regions}")
    for ticker in tickers:
        end = datetime.now()
        start = end - timedelta(days=days)
        try:
            df = fetch_ohlcv(ticker, start_date=start.strftime("%Y-%m-%d"),
                             end_date=end.strftime("%Y-%m-%d"))
            if df is not None and len(df) > 10:
                save_ohlcv(data_dir, ticker, df)
                counts["fetched"] += 1
            else:
                counts["skipped"] += 1
        except Exception as e:
            logger.warning(f"Failed to fetch {ticker}: {e}")
            counts["errors"] += 1
    return counts


def _compute_sentiment(ticker: str, max_articles: int = 10) -> SentimentScore:
    """Compute sentiment score from fetched news articles."""
    articles = fetch_news([ticker], max_articles=max_articles)
    if not articles:
        return SentimentScore(ticker=ticker, score=0.0,
                              positive_count=0, negative_count=0,
                              neutral_count=0, total_articles=0, articles=[])

    # Simple keyword-based sentiment
    POSITIVE_WORDS = {'surge', 'beat', 'upgrade', 'strong', 'growth', 'profit',
                      'gain', 'bullish', 'rally', 'outperform', 'recovery',
                      'record high', 'breakout', 'positive', 'optimistic'}
    NEGATIVE_WORDS = {'decline', 'miss', 'downgrade', 'weak', 'loss', 'crisis',
                      'recession', 'bearish', 'plunge', 'underperform', 'concern',
                      'warn', 'risk', 'fall', 'drop', 'negative'}

    positive = 0
    negative = 0
    neutral = 0
    for a in articles:
        text = (a.title + " " + a.summary).lower()
        pos_count = sum(1 for w in POSITIVE_WORDS if w in text)
        neg_count = sum(1 for w in NEGATIVE_WORDS if w in text)
        if pos_count > neg_count:
            positive += 1
        elif neg_count > pos_count:
            negative += 1
        else:
            neutral += 1

    total = len(articles)
    score = (positive - negative) / total if total > 0 else 0.0
    return SentimentScore(
        ticker=ticker, score=round(score, 3),
        positive_count=positive, negative_count=negative,
        neutral_count=neutral, total_articles=total,
        articles=articles,
    )


# ========================
# Layout
# ========================

header = html.Div([
    html.H1("🚀 Rocket Stock Scanner",
            style={'textAlign': 'center', 'color': '#00e676',
                   'marginBottom': 0, 'marginTop': 10, 'fontSize': 32}),
    html.P("Global Stock Scanner — Sweden · USA · China · India",
           style={'textAlign': 'center', 'color': '#9e9e9e', 'fontSize': 14}),
])

tabs = dbc.Tabs([
    dbc.Tab(label="🏆 Rankings", tab_id="tab-rankings"),
    dbc.Tab(label="📊 Ticker Detail", tab_id="tab-detail"),
    dbc.Tab(label="📈 Backtest", tab_id="tab-backtest"),
    dbc.Tab(label="📰 Sentiment", tab_id="tab-sentiment"),
    dbc.Tab(label="⚙️ Settings", tab_id="tab-settings"),
], id="main-tabs", active_tab="tab-rankings",
    style={'backgroundColor': 'transparent'},
    persistence=True,
    persisted_props=['active_tab'],
    persistence_type='local')

# Rankings tab
rankings_tab = dbc.Container([
    html.Div([
        html.Label("Regions", style={'color': '#e0e0e0'}),
        dcc.Dropdown(
            options=_get_all_region_options(),
            value=DEFAULT_REGIONS, multi=True, id="rank-regions",
            style={'backgroundColor': '#0f3460', 'color': '#e0e0e0'}),
    ], style={'width': '30%', 'display': 'inline-block'}),
    html.Div([
        html.Label("Min Score", style={'color': '#e0e0e0'}),
        dcc.Slider(0, 100, value=0, id="rank-min-score",
                   marks={i: str(i) for i in range(0, 101, 20)},
                   className='rank-slider'),
    ], style={'width': '70%', 'display': 'inline-block', 'paddingLeft': 20}),
    html.Br(),
    dcc.Loading(id="rank-loading", type="default", children=[
        html.Div(id="rank-table-container",
                 style={'marginTop': 20})
    ]),
], fluid=True, style={'backgroundColor': '#0a0a1a', 'minHeight': '90vh', 'color': '#e0e0e0'})

# Detail tab
detail_tab = dbc.Container([
    html.Div([
        html.Label("Ticker", style={'color': '#e0e0e0'}),
        dcc.Dropdown(id="detail-ticker",
                      options=lambda: _build_ticker_dropdown_options(),
                      placeholder="Search ticker...",
                      searchable=True,
                      style={'backgroundColor': '#0f3460', 'color': '#e0e0e0',
                             'width': '40%', 'display': 'inline-block'}),
        html.Label("Period (days)", style={'color': '#e0e0e0', 'marginLeft': 20}),
        dcc.Dropdown(options=[{'label': str(p), 'value': p}
                               for p in [30, 60, 90, 180, 365]],
                      value=180, id="detail-period",
                      style={'backgroundColor': '#0f3460', 'color': '#e0e0e0',
                             'width': '15%', 'display': 'inline-block',
                             'marginLeft': 10}),
    ]),
    html.Div(id="detail-content", style={'marginTop': 20})
], fluid=True, style={'backgroundColor': '#0a0a1a', 'minHeight': '90vh',
                      'color': '#e0e0e0'})

# Backtest tab
backtest_tab = dbc.Container([
    html.Div([
        html.Label("Ticker", style={'color': '#e0e0e0'}),
        dcc.Dropdown(id="back-ticker",
                      options=lambda: _build_ticker_dropdown_options(),
                      placeholder="Select ticker...",
                      searchable=True,
                      style={'backgroundColor': '#0f3460', 'color': '#e0e0e0'}),
        html.Label("Strategy", style={'color': '#e0e0e0', 'marginLeft': 20}),
        dcc.Dropdown(options=[{'label': 'EMA Crossover 9/21', 'value': 'ema_crossover'},
                               {'label': 'RSI Reversal (30/70)', 'value': 'rsi'}],
                      value='ema_crossover', id="back-strategy",
                      style={'backgroundColor': '#0f3460', 'color': '#e0e0e0',
                             'marginLeft': 10}),
        html.Button("Run Backtest", id="back-run", n_clicks=0,
                     style={'marginLeft': 20, 'backgroundColor': '#00e676',
                            'color': '#000', 'border': 'none',
                            'padding': '8px 16px', 'borderRadius': 4}),
    ]),
    html.Div(id="back-content", style={'marginTop': 20})
], fluid=True, style={'backgroundColor': '#0a0a1a', 'minHeight': '90vh',
                      'color': '#e0e0e0'})

# Sentiment tab
sentiment_tab = dbc.Container([
    html.Div([
        html.Label("Ticker", style={'color': '#e0e0e0'}),
        dcc.Dropdown(id="sent-ticker",
                      options=lambda: _build_ticker_dropdown_options(),
                      placeholder="Select ticker...",
                      searchable=True,
                      style={'backgroundColor': '#0f3460', 'color': '#e0e0e0'}),
        html.Button("Fetch Sentiment", id="sent-run", n_clicks=0,
                     style={'marginLeft': 20, 'backgroundColor': '#00e676',
                            'color': '#000', 'border': 'none',
                            'padding': '8px 16px', 'borderRadius': 4}),
    ]),
    html.Div(id="sent-content", style={'marginTop': 20})
], fluid=True, style={'backgroundColor': '#0a0a1a', 'minHeight': '90vh',
                      'color': '#e0e0e0'})

# Settings tab
settings_tab = dbc.Container([
    html.Div([
        html.H3("⚙️ Settings", style={'color': '#00e676'}),
        html.P("Configure data sources and scoring weights."),
        html.Hr(),
        html.Div([
            html.Label("Data Directory", style={'color': '#e0e0e0'}),
            dcc.Input(id="settings-data-dir", value=DATA_DIR,
                        style={'backgroundColor': '#0f3460', 'color': '#e0e0e0',
                               'border': '1px solid #333', 'padding': '5px'}),
        ], style={'marginBottom': 20}),
        html.Div([
            html.H4("Universe", style={'color': '#00e676'}),
            html.P("1225+ tickers across 4 regions:", style={'color': '#e0e0e0', 'fontSize': 13}),
            html.Ul([
                html.Li("🇸🇪 Sweden: ~233 tickers (Nasdaq Stockholm)"),
                html.Li("🇺🇸 USA: ~459 tickers (S&P 500 + growth)"),
                html.Li("🇨🇳 China: ~233 tickers (A-shares + Hong Kong + ADS)"),
                html.Li("🇮🇳 India: ~300 tickers (NIFTY 500+)"),
            ], style={'color': '#9e9e9e', 'fontSize': 12, 'marginBottom': 20}),
        ], style={'marginBottom': 20}),
        html.Div([
            html.H4("Scoring Weights (used by SignalCombiner + weighter)"),
            html.P("Momentum 40% · Trend 30% · Volatility 20% · Volume 10%",
                   style={'color': '#9e9e9e', 'fontSize': 13}),
        ], style={'marginBottom': 20}),
        html.Button("Run Data Update (all regions, 365 days, ~1225 tickers)",
                     id="run-update", n_clicks=0,
                     style={'backgroundColor': '#2962ff', 'color': '#fff',
                            'border': 'none', 'padding': '10px 20px',
                            'borderRadius': 4, 'marginTop': 20}),
        html.Div(id="update-status", style={'marginTop': 15, 'color': '#e0e0e0'}),
    ], style={'maxWidth': 600})
], fluid=True, style={'backgroundColor': '#0a0a1a', 'minHeight': '90vh',
                      'color': '#e0e0e0'})

app.layout = html.Div([
    header,
    html.Br(),
    tabs,
    html.Div(id="page-content", style={'display': 'none'}),
    dcc.Interval(id="auto-refresh", interval=5 * 60 * 1000, n_intervals=0),  # 5 min
])

# ========================
# Tab rendering
# ========================

@callback(
    Output("page-content", "children"),
    Input("main-tabs", "active_tab"),
)
def render_page(tab):
    if tab == "tab-rankings":
        return rankings_tab
    elif tab == "tab-detail":
        return detail_tab
    elif tab == "tab-backtest":
        return backtest_tab
    elif tab == "tab-sentiment":
        return sentiment_tab
    elif tab == "tab-settings":
        return settings_tab
    return html.Div("Unknown tab")


# ========================
# Rankings callback
# ========================

@callback(
    Output("rank-table-container", "children"),
    Input("rank-regions", "value"),
    Input("rank-min-score", "value"),
    Input("auto-refresh", "n_intervals"),
)
def update_rankings(regions, min_score, n):
    """Update rankings table."""
    tickers = _get_universe_for_regions(regions)
    if not tickers:
        return html.P("No tickers selected", style={'color': '#9e9e9e'})

    # Score all tickers
    results = []
    for ticker in tickers:
        try:
            df = load_ohlcv(DATA_DIR, ticker)
            if df is None or len(df) < 20:
                continue

            summary, indicator_results = _compute_all_indicators(df)
            score_data = _score_from_summary(summary, ticker)

            rocket_score = score_data["rocket_score"]
            total_score = rocket_score.overall_score
            if total_score < min_score:
                continue

            results.append({
                'ticker': ticker,
                'score': total_score,
                'momentum': rocket_score.momentum_score,
                'trend': rocket_score.trend_score,
                'volatility': rocket_score.volatility_score,
                'volume': rocket_score.volume_score,
                'buy_count': summary.buy_count,
                'sell_count': summary.sell_count,
                'hold_count': summary.hold_count,
                'close': round(float(df['close'].iloc[-1]), 2) if 'close' in df.columns else 0,
            })
        except Exception as e:
            logger.warning(f"Failed to score {ticker}: {e}")
            continue

    if not results:
        return html.P("No tickers match criteria — fetch data first (Settings tab)",
                       style={'color': '#9e9e9e'})

    results.sort(key=lambda x: x['score'], reverse=True)

    rows = []
    for r in results:
        score_color = '#00e676' if r['score'] >= 70 else '#ff9800' if r['score'] >= 40 else '#ef5350'
        rows.append(dbc.TableRow([
            dbc.TableCol(r['ticker']),
            dbc.TableCol(html.Span(f"{r['score']:.1f}",
                                   style={'color': score_color, 'fontWeight': 'bold'})),
            dbc.TableCol(f"{r['momentum']:.1f}"),
            dbc.TableCol(f"{r['trend']:.1f}"),
            dbc.TableCol(f"{r['volatility']:.1f}"),
            dbc.TableCol(f"{r['volume']:.1f}"),
            dbc.TableCol(f"{r['buy_count']}/{r['sell_count']}/{r['hold_count']}"),
            dbc.TableCol(f"{r['close']:.2f}"),
        ]))

    return html.Div([
        html.H4(f"🏆 Rankings — {len(results)} stocks scored",
                style={'color': '#e0e0e0'}),
        dbc.Table([
            dbc.Thead(dbc.Tr([
                dbc.TableTh("Ticker"), dbc.TableTh("Score"),
                dbc.TableTh("Momentum"), dbc.TableTh("Trend"),
                dbc.TableTh("Volatility"), dbc.TableTh("Volume"),
                dbc.TableTh("B/S/H"), dbc.TableTh("Close"),
            ], style={'backgroundColor': '#0f3460'})),
            dbc.Tbody(rows),
        ], bordered=True, dark=True, striped=True, hover=True),
    ])


# ========================
# Detail callback
# ========================

@callback(
    Output("detail-content", "children"),
    Input("detail-ticker", "value"),
    Input("detail-period", "value"),
)
def update_detail(ticker, period):
    """Update ticker detail view."""
    if not ticker:
        return html.P("Select a ticker to view details",
                       style={'color': '#9e9e9e'})

    try:
        df = load_ohlcv(DATA_DIR, ticker)
        if df is None:
            return html.P(f"No data found for {ticker}. Go to Settings and run Data Update first.",
                           style={'color': '#ef5350'})

        df = df.iloc[-period:].copy()
        if len(df) < 5:
            return html.P("Not enough data for this period",
                           style={'color': '#ef5350'})

        summary, indicator_results = _compute_all_indicators(df)
        score_data = _score_from_summary(summary, ticker)
        rocket_score = score_data["rocket_score"]

        # Price info
        last_close = float(df['close'].iloc[-1])
        prev_close = float(df['close'].iloc[-2]) if len(df) > 1 else last_close
        change_pct = ((last_close - prev_close) / prev_close * 100) if prev_close else 0
        change_color = '#00e676' if change_pct >= 0 else '#ef5350'

        # Convert indicator results to dicts for plotting
        ind_dicts = _indicator_values_to_dicts(indicator_results)

        # Candlestick chart
        fig_candle = create_candlestick(df, ticker=ticker, title="Price Action")
        # Add indicator overlays
        fig_indicators = add_indicators_to_chart(fig_candle, ind_dicts, df)

        # Score cards
        cards = [
            _make_gauge(round(rocket_score.momentum_score, 1), "Momentum",
                         '#00e676' if rocket_score.momentum_score > 50 else '#ef5350'),
            _make_gauge(round(rocket_score.trend_score, 1), "Trend",
                         '#00e676' if rocket_score.trend_score > 50 else '#ef5350'),
            _make_gauge(round(rocket_score.volatility_score, 1), "Volatility",
                         '#00e676' if rocket_score.volatility_score > 50 else '#ef5350'),
            _make_gauge(round(rocket_score.volume_score, 1), "Volume",
                         '#00e676' if rocket_score.volume_score > 50 else '#ef5350'),
            _make_gauge(round(rocket_score.overall_score, 1), "Overall Score",
                         '#00e676' if rocket_score.overall_score > 50 else '#ef5350'),
        ]

        # Signal details table
        detail_rows = []
        for d in summary.details[:20]:  # Show first 20 indicators
            sig_color = '#00e676' if d['signal'] == 'BUY' else '#ef5350' if d['signal'] == 'SELL' else '#9e9e9e'
            detail_rows.append(dbc.TableRow([
                dbc.TableCol(d['name']),
                dbc.TableCol(html.Span(d['signal'],
                                       style={'color': sig_color,
                                              'fontWeight': 'bold'})),
                dbc.TableCol(f"{d['score']:.3f}"),
                dbc.TableCol(d['category']),
            ]))

        return html.Div([
            html.Div([
                html.H3(f"{ticker} — {last_close:.2f}  ({change_pct:+.2f}%)",
                        style={'color': '#e0e0e0'}),
                html.Div([
                    html.Span(f"High: {df['high'].max():.2f}",
                              style={'color': '#9e9e9e', 'marginRight': 15}),
                    html.Span(f"Low: {df['low'].min():.2f}",
                              style={'color': '#9e9e9e', 'marginRight': 15}),
                    html.Span(f"Vol: {df['volume'].iloc[-1]:,.0f}",
                              style={'color': '#9e9e9e'}),
                ], style={'marginBottom': 15}),
            ]),
            html.Div(cards, style={'display': 'flex', 'gap': 8,
                                   'marginBottom': 20, 'flexWrap': 'wrap'}),
            dcc.Graph(figure=fig_indicators,
                       config={'displayModeBar': False}),
            html.Br(),
            html.Div([
                html.H5("📊 Indicator Breakdown",
                        style={'color': '#e0e0e0'}),
                dbc.Table([
                    dbc.Thead(dbc.Tr([
                        dbc.TableTh("Indicator"),
                        dbc.TableTh("Signal"),
                        dbc.TableTh("Score"),
                        dbc.TableTh("Category"),
                    ], style={'backgroundColor': '#0f3460'})),
                    dbc.Tbody(detail_rows),
                ], bordered=True, dark=True, striped=True, hover=True),
            ], style={'marginTop': 20}),
        ])

    except Exception as e:
        logger.exception(f"Detail error for {ticker}")
        return html.P(f"Error loading {ticker}: {e}",
                       style={'color': '#ef5350'})


# ========================
# Backtest callback
# ========================

@callback(
    Output("back-content", "children"),
    Input("back-run", "n_clicks"),
    Input("back-ticker", "value"),
    Input("back-strategy", "value"),
)
def update_backtest(n_clicks, ticker, strategy_name):
    """Update backtest view."""
    if not ticker or not n_clicks:
        return html.P("Select a ticker and click Run Backtest",
                       style={'color': '#9e9e9e'})

    try:
        df = load_ohlcv(DATA_DIR, ticker)
        if df is None:
            return html.P(f"No data found for {ticker}",
                           style={'color': '#ef5350'})

        # Build strategy
        if strategy_name == 'ema_crossover':
            strategy = EMACrossoverStrategy(fast=9, slow=21)
        elif strategy_name == 'rsi':
            strategy = RSIBasedStrategy(period=14,
                                         buy_threshold=30,
                                         sell_threshold=70)
        else:
            return html.P("Unknown strategy",
                           style={'color': '#ef5350'})

        bt_result = run_backtest(df, strategy=strategy,
                                  initial_capital=100000.0)

        metrics = bt_result.metrics

        # Equity chart
        eq_fig = create_equity_curve(
            bt_result.equity_curve,
            title=f"{ticker} — {strategy.name}",
            dates=bt_result.dates,
            initial_capital=100000.0,
        )

        # Trade table
        trade_rows = []
        for t in bt_result.trades[:30]:
            t_type = t.action
            t_color = '#00e676' if t_type == 'BUY' else '#ef5350'
            trade_rows.append(dbc.TableRow([
                dbc.TableCol(html.Span(t_type,
                                       style={'color': t_color,
                                              'fontWeight': 'bold'})),
                dbc.TableCol(f"${t.price:.2f}"),
                dbc.TableCol(f"${t.commission:.2f} commission"),
                dbc.TableCol(str(t.date)[:10] if t.date else "—"),
            ]))

        return html.Div([
            html.Div([
                _make_gauge(round(metrics['total_return_pct'], 1),
                             "Return %",
                             '#00e676' if metrics['total_return_pct'] > 0
                             else '#ef5350'),
                _make_gauge(round(metrics['sharpe_ratio'], 2),
                             "Sharpe",
                             '#00e676' if metrics['sharpe_ratio'] > 1
                             else '#ff9800'),
                _make_gauge(round(100 - abs(
                    metrics['max_drawdown_pct']), 1),
                             f"Max DD: {metrics['max_drawdown_pct']:.1f}%",
                             '#00e676' if metrics[
                                 'max_drawdown_pct'] > -10
                             else '#ef5350'),
                _make_gauge(round(metrics['win_rate_pct'], 1),
                             "Win Rate %",
                             '#00e676' if metrics['win_rate_pct'] > 50
                             else '#ef5350'),
            ], style={'display': 'flex', 'gap': 8,
                       'marginBottom': 20, 'flexWrap': 'wrap'}),
            html.Div([
                html.H5(f"📈 Equity Curve — {strategy.name}",
                        style={'color': '#e0e0e0'}),
            ], style={'marginBottom': 10}),
            dcc.Graph(figure=eq_fig,
                       config={'displayModeBar': False}),
            html.Br(),
            html.Div([
                html.H5(f"📋 Trades ({len(bt_result.trades)})",
                        style={'color': '#e0e0e0'}),
                dbc.Table([
                    dbc.Thead(dbc.Tr([
                        dbc.TableTh("Type"),
                        dbc.TableTh("Price"),
                        dbc.TableTh("Commission"),
                        dbc.TableTh("Date"),
                    ], style={'backgroundColor': '#0f3460'})),
                    dbc.Tbody(trade_rows),
                ], bordered=True, dark=True, striped=True, hover=True),
            ], style={'marginTop': 20}),
        ])

    except Exception as e:
        logger.exception(f"Backtest error for {ticker}")
        return html.P(f"Backtest error: {e}",
                       style={'color': '#ef5350'})


# ========================
# Sentiment callback
# ========================

@callback(
    Output("sent-content", "children"),
    Input("sent-run", "n_clicks"),
    Input("sent-ticker", "value"),
)
def update_sentiment(n_clicks, ticker):
    """Update sentiment view."""
    if not ticker or not n_clicks:
        return html.P("Select a ticker and click Fetch Sentiment",
                       style={'color': '#9e9e9e'})

    try:
        result = _compute_sentiment(ticker, max_articles=15)

        score_color = '#00e676' if result.score > 0.1 else '#ef5350' if result.score < -0.1 else '#ff9800'

        content = html.Div([
            html.H3(f"Sentiment: {ticker}",
                    style={'color': '#e0e0e0'}),
            html.H1(f"{result.score:+.3f}",
                    style={'color': score_color,
                           'textAlign': 'center'}),
            html.P(f"{result.total_articles} articles analyzed",
                   style={'textAlign': 'center',
                          'color': '#9e9e9e'}),
            html.Div([
                html.Span(f"✅ Positive: {result.positive_count} ",
                           style={'color': '#00e676'}),
                html.Span(f"⚠️ Neutral: {result.neutral_count} ",
                           style={'color': '#ff9800'}),
                html.Span(f"❌ Negative: {result.negative_count}",
                           style={'color': '#ef5350'}),
            ], style={'textAlign': 'center', 'marginBottom': 15}),
            html.Hr(),
            html.H4("📰 Latest Articles",
                    style={'color': '#e0e0e0'}),
        ])

        article_divs = []
        for a in result.articles[:10]:
            article_divs.append(html.Div([
                html.A(a.title, href=a.url if a.url else "#",
                       target="_blank",
                       style={'color': '#89b4fa', 'fontSize': 14}),
                html.P(a.summary[:150] + "..." if len(
                    a.summary) > 150 else a.summary,
                       style={'color': '#9e9e9e', 'fontSize': 12,
                              'marginBottom': 0}),
            ], style={'marginBottom': 10,
                       'padding': '8px',
                       'backgroundColor': '#1a1a2e',
                       'borderRadius': 4}))

        content.children.append(html.Div(article_divs))
        return content

    except Exception as e:
        logger.exception(f"Sentiment error for {ticker}")
        return html.P(f"Sentiment error: {e}",
                       style={'color': '#ef5350'})


# ========================
# Data update callback
# ========================

@callback(
    Output("update-status", "children"),
    Input("run-update", "n_clicks"),
    Input("settings-data-dir", "value"),
    prevent_initial_call=True,
)
def run_data_update(n, data_dir):
    """Trigger data update."""
    if not n:
        return ""
    try:
        counts = _refresh_data(data_dir,
                                regions=["sweden", "usa", "china", "india"],
                                days=365)
        return html.Div([
            html.Span(
                f"✅ Fetch complete: {counts['fetched']} fetched, "
                f"{counts['skipped']} skipped, "
                f"{counts['errors']} errors",
                style={'color': '#00e676'}),
        ])
    except Exception as e:
        logger.exception("Data update error")
        return html.Div([
            html.Span(f"❌ Error: {e}",
                       style={'color': '#ef5350'}),
        ])


if __name__ == "__main__":
    logger.info("🚀 Starting Rocket Stock Scanner on http://localhost:8050")
    app.run(host="0.0.0.0", port=8050, debug=False)
