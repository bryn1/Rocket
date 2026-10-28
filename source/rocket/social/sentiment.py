"""Social sentiment analysis for Rocket Scanner.

Scrapes r/wallstreetbets and analyzes sentiment for stock tickers.
Uses web_extract (Firecrawl) to fetch Reddit content.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from rocket.social.models import (
    PostData,
    SentimentScore,
    TickerSentiment,
)
def scrape_r_wallstreetbets(limit: int = 25,
                             social_timeout: float = 30.0) -> list[PostData]:
    """Scrape top posts from r/wallstreetbets using web_extract (Firecrawl).

    Uses AltIndex as a reliable WSB tracker since direct Reddit scraping is blocked.
    Note: This function should be called from main thread where web_extract is available.

    Args:
        limit: Number of top posts to scrape
        social_timeout: httpx per-call timeout in seconds (MC 11546 B1; default
            30.0 preserves the pre-B1 behaviour for every existing caller)

    Returns:
        List of PostData objects with scraped content
    """

    # Use web_extract via the API (Firecrawl backend)
    url = "https://altindex.com/wallstreetbets"

    try:
        # Call web_extract API to get content
        # The web_extract tool uses Firecrawl as backend
        import httpx
        response = httpx.post(
            "http://localhost:8000/api/v1/extract",
            json={
                'urls': [url],
                'formats': ['markdown'],
            },
            timeout=social_timeout
        )

        if response.status_code != 200:
            return []

        data = response.json()
        if 'results' not in data or not data['results']:
            return []

        content = data['results'][0].get('content', '')
        if not content or len(content) < 100:
            return []

    except Exception as e:
        # Fallback if web_extract fails
        content = f"WSB data unavailable: {e}"

    # Parse the markdown content to extract posts
    posts = []

    # Look for post patterns in the content
    # WSB table format: Company | Ticker | Mentions | Sentiment | Price | AI Score
    # Example: | [![Micron Technology](...)](...) | [###### Micron Technology<br><br>MU](...) | 389  <br>62.8% | Bearish | $970.82  <br>12.2% | 71  |
    post_pattern = re.compile(
        r'\|\s*\[!\[.*?\]\(.*?\)\]\(.*?\)\s*\]\(.*?\)\s*\|\s*\[######\s*(.*?)<br><br>(\w+)]\(',
        re.DOTALL
    )

    for match in post_pattern.finditer(content):
        company = match.group(1).strip()
        ticker = match.group(2).strip().upper()

        if not ticker or len(ticker) < 2:
            continue

        post = PostData(
            title=f"WSB Trending: {company}",
            author="altindex",
            score=0,
            url=f"https://altindex.com/ticker/{ticker.lower()}",
            timestamp=datetime.now(timezone.utc),
            extracted_tickers=[ticker],
        )
        posts.append(post)

    return posts[:limit]


def extract_tickers_from_text(text: str | None) -> list[str]:
    """Extract stock tickers from text using regex patterns.
    
    Args:
        text: Text to extract tickers from
        
    Returns:
        List of ticker symbols found in text
    """
    if not text or not isinstance(text, str):
        return []

    # Clean up text
    text = re.sub(r'<.*?>', '', text)  # Remove HTML tags
    text = re.sub(r'\s+', ' ', text)  # Normalize whitespace

    # Ticker patterns: 1-5 uppercase letters, possibly with dots
    patterns = [
        r'\b([A-Z]{1,4}\.[A-Z])\b',  # e.g., BRK.A, BRK.B
        r'\b([A-Z]{2,5})\b',          # 2-5 uppercase letters
    ]

    tickers = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            ticker = match.group(1).strip()
            if ticker and len(ticker) >= 2:
                tickers.add(ticker)

    # Remove common non-ticker words
    exclude = {
        'THE', 'AND', 'FOR', 'NOT', 'BUT', 'ANY', 'ALL', 'CAN', 'HAD',
        'HAS', 'HIS', 'HER', 'ITS', 'THEY', 'THEM', 'THEN', 'THAT',
        'THIS', 'WITH', 'FROM', 'TO', 'IN', 'ON', 'AT', 'BY', 'OF',
    }
    tickers -= exclude

    return sorted(tickers)


def analyze_sentiment(text: str | None) -> dict[str, int]:
    """Analyze sentiment of text for bullish/bearish/neutral signals.
    
    Args:
        text: Text to analyze
        
    Returns:
        Dictionary with counts of bullish, bearish, and neutral signals
    """
    if not text or not isinstance(text, str):
        return {'bullish': 0, 'bearish': 0, 'neutral': 1}

    # Clean text
    text_lower = text.lower()

    # Bullish indicators
    bullish_words = [
        'call', 'calls', 'buy', 'bull', 'moon', 'rocket', 'long',
        'bagholder', 'diamond', 'hodl', 'pump', 'wagie', 'aper',
        'dividend', 'dividends', 'yield', 'growth', 'surge', 'gain',
        'gains', 'rally', 'recovery', 'breakout', 'momentum',
    ]

    # Bearish indicators
    bearish_words = [
        'put', 'puts', 'sell', 'bear', 'crash', 'dump', 'short',
        'loss', 'losses', 'bleed', 'blood', 'doom', 'apocalypse',
        'recession', 'crisis', 'correction', 'drop', 'decline',
        'down', 'downgrade', 'fear', 'panic', 'liquidate',
    ]

    # Count occurrences
    bullish_count = sum(text_lower.count(word) for word in bullish_words)
    bearish_count = sum(text_lower.count(word) for word in bearish_words)

    # Neutral if no clear sentiment
    if bullish_count == 0 and bearish_count == 0:
        return {'bullish': 0, 'bearish': 0, 'neutral': 1}

    return {
        'bullish': bullish_count,
        'bearish': bearish_count,
        'neutral': max(0, 1 - bullish_count - bearish_count),
    }


def analyze_ticker_sentiment(posts: list[PostData]) -> dict[str, TickerSentiment]:
    """Analyze sentiment for each ticker mentioned in posts.
    
    Args:
        posts: List of scraped posts
        
    Returns:
        Dictionary mapping tickers to their sentiment data
    """
    ticker_data: dict[str, dict[str, Any]] = {}

    for post in posts:
        # Analyze sentiment of the post title
        sentiment_counts = analyze_sentiment(post.title)

        # Associate with each ticker mentioned
        for ticker in post.extracted_tickers:
            if ticker not in ticker_data:
                ticker_data[ticker] = {
                    'total': 0,
                    'bullish': 0,
                    'bearish': 0,
                    'neutral': 0,
                    'titles': [],
                }

            ticker_data[ticker]['total'] += 1
            ticker_data[ticker]['bullish'] += sentiment_counts['bullish']
            ticker_data[ticker]['bearish'] += sentiment_counts['bearish']
            ticker_data[ticker]['neutral'] += sentiment_counts['neutral']
            ticker_data[ticker]['titles'].append(post.title)

    # Build result
    result = {}
    for ticker, data in ticker_data.items():
        # Calculate sentiment score (-1.0 to +1.0)
        total_mentions = data['total']
        if total_mentions == 0:
            sentiment_score = 0.0
            sentiment_label = 'neutral'
        else:
            # Score based on bullish vs bearish ratio
            net_sentiment = data['bullish'] - data['bearish']
            sentiment_score = round(net_sentiment / total_mentions, 3)
            sentiment_score = max(-1.0, min(1.0, sentiment_score))

            if sentiment_score > 0.3:
                sentiment_label = 'bullish'
            elif sentiment_score < -0.3:
                sentiment_label = 'bearish'
            else:
                sentiment_label = 'neutral'

        result[ticker] = TickerSentiment(
            ticker=ticker,
            total_mentions=total_mentions,
            bullish_mentions=data['bullish'],
            bearish_mentions=data['bearish'],
            neutral_mentions=data['neutral'],
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            sample_titles=data['titles'][:3],  # Limit sample titles
        )

    return result


def get_social_sentiment(tickers: list[str],
                         social_timeout: float = 30.0) -> dict[str, SentimentScore]:
    """Get social sentiment for a list of tickers from WSB.
    
    Args:
        tickers: List of ticker symbols to analyze
        social_timeout: httpx per-call timeout in seconds passed to the scrape
            (MC 11546 B1; default 30.0 preserves the pre-B1 behaviour)
        
    Returns:
        Dictionary mapping tickers to their social sentiment data
    """
    # Scrape WSB posts
    posts = scrape_r_wallstreetbets(limit=50, social_timeout=social_timeout)

    # Analyze sentiment for all mentioned tickers
    all_sentiments = analyze_ticker_sentiment(posts)

    # Filter for requested tickers
    result = {}
    for ticker in tickers:
        if ticker in all_sentiments:
            ts = all_sentiments[ticker]
            # Convert to SentimentScore format for Rocket integration
            result[ticker] = SentimentScore(
                ticker=ticker,
                overall_sentiment=ts.sentiment_label,
                sentiment_score=(ts.sentiment_score + 1.0) / 2.0,  # Convert -1..1 to 0..1
                total_mentions=ts.total_mentions,
                bullish_mentions=ts.bullish_mentions,
                bearish_mentions=ts.bearish_mentions,
                neutral_mentions=ts.neutral_mentions,
                sample_titles=ts.sample_titles,
            )
        else:
            # No sentiment data for this ticker
            result[ticker] = SentimentScore(
                ticker=ticker,
                overall_sentiment='neutral',
                sentiment_score=0.5,  # Neutral score
                total_mentions=0,
                bullish_mentions=0,
                bearish_mentions=0,
                neutral_mentions=0,
                sample_titles=[],
            )

    return result


def get_social_score(sentiment: SentimentScore) -> float:
    """Convert SentimentScore to a single score between -1 and 1.
    
    -1 = very bearish, 0 = neutral, 1 = very bullish
    
    Args:
        sentiment: SentimentScore to convert
        
    Returns:
        Single score representing overall sentiment
    """
    # sentiment_score is 0.0 to 1.0, convert to -1.0 to 1.0
    return sentiment.sentiment_score * 2.0 - 1.0
