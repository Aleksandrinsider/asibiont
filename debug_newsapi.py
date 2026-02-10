#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Отладка NewsAPI конфигурации
"""

import asyncio
import sys
import os
import logging
import requests
from datetime import datetime, timedelta
from config import NEWSAPI_API_KEY

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def debug_newsapi():
    """Отладка NewsAPI"""
    print("DEBUG: NewsAPI Configuration")
    print("=" * 50)
    
    # Check API key
    print(f"API Key: {'✅ Configured' if NEWSAPI_API_KEY else '❌ Missing'}")
    if not NEWSAPI_API_KEY:
        print("❌ NEWSAPI_API_KEY not found in config")
        return
    
    print(f"Key prefix: {NEWSAPI_API_KEY[:8]}...")
    print()
    
    # Test direct API call
    test_queries = [
        "technology",  # Very general
        "AI",          # Short
        "business"     # General business
    ]
    
    for query in test_queries:
        print(f"Testing query: '{query}'")
        
        # Try different date ranges
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)
        
        url = "https://newsapi.org/v2/everything"
        
        for days_back, label in [(1, "1 day"), (3, "3 days"), (7, "7 days")]:
            from_date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            
            params = {
                'q': query,
                'from': from_date,
                'sortBy': 'publishedAt',
                'apiKey': NEWSAPI_API_KEY,
                'language': 'en',  # Try English first
                'pageSize': 5
            }
            
            try:
                response = requests.get(url, params=params, timeout=10)
                data = response.json()
                
                if response.status_code == 200:
                    total = data.get('totalResults', 0)
                    articles = len(data.get('articles', []))
                    print(f"   {label}: {total} total, {articles} returned")
                    
                    if articles > 0:
                        first_article = data['articles'][0]
                        print(f"   Example: {first_article['title'][:60]}...")
                else:
                    print(f"   {label}: Error {response.status_code} - {data.get('message', 'Unknown error')}")
            
            except Exception as e:
                print(f"   {label}: Exception - {e}")
        
        print("-" * 30)
    
    # Test API limits
    print("Testing API limits...")
    url = "https://newsapi.org/v2/everything"
    params = {
        'q': 'test',
        'apiKey': NEWSAPI_API_KEY,
        'pageSize': 1
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if response.status_code == 200:
            print("✅ API is accessible")
            print(f"Total results for 'test': {data.get('totalResults', 0)}")
        elif response.status_code == 426:
            print("⚠️  Free plan - upgrade required for latest news")
        elif response.status_code == 429:
            print("⚠️  Rate limit exceeded")
        else:
            print(f"❌ API Error: {response.status_code} - {data.get('message')}")
    
    except Exception as e:
        print(f"❌ Connection error: {e}")

if __name__ == "__main__":
    asyncio.run(debug_newsapi())