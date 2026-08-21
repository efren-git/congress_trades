# analysis_framework.py

import pandas as pd
import yfinance as yf
from datetime import timedelta
import requests
from congress_db import CongressDataBase

class CongressData:
    def __init__(self, kadoa_db: str = "https://www.kadoa.com/congress/data/"):
        self.kadoa_db = kadoa_db

    def filers(self):
        """Get all filers from Kadoa API"""
        response = requests.get(f"{self.kadoa_db}filers.json")
        return pd.DataFrame(response.json())

    def trades(self):
        """Get all trades from Kadoa API"""
        response = requests.get(f"{self.kadoa_db}trades.json")
        return pd.DataFrame(response.json())

    def filer_trades(self, filer_id: str):
        """Get trades for a specific filer"""
        response = requests.get(f"{self.kadoa_db}filer/{filer_id}.json")
        data = response.json()
        trades_df = pd.DataFrame(data.get("trades", []))
        filer_info_df = pd.DataFrame([data.get("filer", {})])
        return filer_info_df, trades_df

    def update_trades(self, congress_db: CongressDataBase):
        """Update the local database with the latest trades"""
        trades_df = self.trades()
        trades_df["notification_date"] = None
        trades_df["ingested_at"] = None
        trades_df["filing_id"] = trades_df["id"].str.replace(r'_[a-zA-Z]+\d+$', '', regex=True)
        
        # Ensure all required columns exist, fill missing with None/appropriate defaults
        required_columns = [
            "id", "filing_id", "filer_id", "source_id", "transaction_date",
            "notification_date", "filing_date", "owner", "ticker", "asset_name",
            "asset_type", "transaction_type", "amount_range_low",
            "amount_range_high", "amount_range_label", "comment",
            "is_late", "days_to_file", "row_index",
            "notification_received_over_30d", "doc_url",
            "filing_type", "ret_since", "excess_since",
            "ret_30d", "ret_1y"
        ]
        
        for col in required_columns:
            if col not in trades_df.columns:
                trades_df[col] = None
        
        trades_to_insert = trades_df[required_columns].copy()
        congress_db.insert_trades(trades_to_insert)

    def update_filers(self, congress_db: CongressDataBase):
        """Update the local database with the latest filers"""
        filers_df = self.filers()
        print(filers_df.columns.to_list())
        print(filers_df.head())
        congress_db.insert_filers(filers_df)


class CongressTradeAnalyzer:
    def __init__(self, congress_db):
        self.db = congress_db
        self._price_cache = {}  # Simple cache for prices

    def _get_price(self, ticker, date):
        """Get price with caching to avoid repeated API calls"""
        # Create cache key
        cache_key = f"{ticker}_{date.strftime('%Y-%m-%d')}"
        
        # Check cache first
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]
        
        try:
            # Download data for a broader window in case date falls on weekend/holiday
            price_data = yf.download(
                ticker, 
                start=date - timedelta(days=5),
                end=date + timedelta(days=5),
                progress=False,
                auto_adjust=True
            )
            
            if price_data.empty:
                self._price_cache[cache_key] = None
                return None
            
            # Handle MultiIndex columns
            if isinstance(price_data.columns, pd.MultiIndex):
                close_col = ('Close', ticker)
            else:
                close_col = 'Close'
            
            if close_col not in price_data.columns:
                self._price_cache[cache_key] = None
                return None
            
            closes = price_data[close_col].dropna()
            if len(closes) == 0:
                self._price_cache[cache_key] = None
                return None
            
            # Get closest date
            closest_idx = (closes.index - date).abs().argmin()
            price = float(closes.iloc[closest_idx])
            
            # Cache the result
            self._price_cache[cache_key] = price
            return price
            
        except Exception as e:
            self._price_cache[cache_key] = None
            return None
    
    def _estimate_amount(self, low, high, estimation='mid'):
        """Estimate dollar amount from range"""
        if pd.isna(low) or pd.isna(high):
            return None
            
        if estimation == 'mid':
            return (low + high) / 2
        elif estimation == 'high':
            return high
        elif estimation == 'low':
            return low
        else:
            return (low + high) / 2
    
    def calculate_lag_analysis(self):
        """Analysis 1: Does filing delay correlate with returns?"""
        query = """
            SELECT 
                ticker,
                transaction_date,
                filing_date,
                days_to_file,
                ret_since,
                excess_since,
                ret_30d,
                ret_1y,
                transaction_type,
                amount_range_low,
                amount_range_high
            FROM raw_trade
            WHERE ticker IS NOT NULL 
            AND transaction_type IN ('Purchase', 'Sale')
        """
        
        trades = self.db.sql_to_pandas(query)
        
        # Calculate correlation between days_to_file and returns
        late_vs_return = trades.groupby(
            pd.qcut(trades['days_to_file'], q=5)
        )['ret_since'].mean()
        
        # Flag suspicious trades: high returns + long filing delay
        trades['suspicious'] = (
            (trades['days_to_file'] > trades['days_to_file'].quantile(0.75)) &
            (trades['ret_since'] > trades['ret_since'].quantile(0.75))
        )
        
        return trades[trades['suspicious']]

    def analyze_sell_timing(self):
        """Analysis 3: Do they sell before bad news?"""
        query = """
            SELECT 
                ticker,
                transaction_date,
                ret_30d,
                excess_since,
                days_to_file,
                transaction_type
            FROM raw_trade
            WHERE transaction_type IN ('Sale (Partial)', 'Sale (Full)')
            AND ticker IS NOT NULL
            AND ret_30d IS NOT NULL
        """
        
        sells = self.db.sql_to_pandas(query)
        
        if len(sells) == 0:
            print("No sell transactions found")
            return pd.DataFrame()
        
        # If they sold and the stock dropped significantly after...
        sells['good_sell'] = sells['ret_30d'] < -0.05  # 5% drop in next 30 days
        
        good_sells = sells[sells['good_sell']].sort_values('ret_30d')
        
        print(f"Found {len(good_sells)} well-timed sells out of {len(sells)} total sells")
        print(f"Success rate: {len(good_sells)/len(sells)*100:.1f}%")
        
        return good_sells
    
    def bet_size_vs_performance(self):
        """Analysis 4: Does bet size correlate with performance?"""
        query = """
            SELECT 
                ticker,
                transaction_date,
                transaction_type,
                amount_range_low,
                amount_range_high,
                ret_since,
                excess_since,
                ret_30d,
                ret_1y
            FROM raw_trade
            WHERE ticker IS NOT NULL
            AND transaction_type IN ('Purchase', 'Sale (Partial)', 'Sale (Full)')
        """
        
        trades = self.db.sql_to_pandas(query)
        
        # Estimate bet size
        trades['estimated_amount'] = trades.apply(
            lambda x: self._estimate_amount(
                x['amount_range_low'], 
                x['amount_range_high']
            ), 
            axis=1
        )
        
        # Filter out None/NaN amounts
        trades = trades.dropna(subset=['estimated_amount'])
        
        if len(trades) < 4:
            print("Not enough trades with valid amounts for bet size analysis")
            return pd.DataFrame()
        
        # Try qcut with duplicates handled
        try:
            trades['bet_size_bucket'] = pd.qcut(
                trades['estimated_amount'], 
                q=4, 
                labels=['Small', 'Medium', 'Large', 'Very Large'],
                duplicates='drop'  # This is the fix!
            )
        except ValueError as e:
            print(f"Could not create quartiles: {e}")
            # Fall back to equal-width bins
            trades['bet_size_bucket'] = pd.cut(
                trades['estimated_amount'],
                bins=4,
                labels=['Small', 'Medium', 'Large', 'Very Large']
            )
        
        return trades.groupby('bet_size_bucket', observed=False)['ret_since'].agg(['mean', 'std', 'count'])

    def explore_transaction_types(self):
        """Explore what transaction types exist in the database"""
        query = """
            SELECT 
                transaction_type,
                COUNT(*) as count,
                AVG(ret_since) as avg_return,
                AVG(days_to_file) as avg_filing_delay
            FROM raw_trade
            WHERE ticker IS NOT NULL
            GROUP BY transaction_type
            ORDER BY count DESC
        """
        
        types = self.db.sql_to_pandas(query)
        print("Transaction types in database:")
        print(types)
        return types

#

    def portfolio_reconstruction(self, filer_id, start_date=None):
        """Reconstruct a filer's portfolio over time"""
        query = f"""
            SELECT 
                transaction_date,
                ticker,
                transaction_type,
                amount_range_low,
                amount_range_high
            FROM raw_trade
            WHERE filer_id = '{filer_id}'
            AND ticker IS NOT NULL
            AND ticker != ''
            ORDER BY transaction_date
        """
        
        trades = self.db.sql_to_pandas(query)
        
        if len(trades) == 0:
            print(f"No trades found for filer {filer_id}")
            return pd.DataFrame()
        
        # Get unique tickers and date ranges
        trades['transaction_date'] = pd.to_datetime(trades['transaction_date'])
        
        # Download all unique tickers in batch
        unique_tickers = trades['ticker'].unique().tolist()
        print(f"Downloading prices for {len(unique_tickers)} unique tickers...")
        
        # Get date range for all trades
        min_date = trades['transaction_date'].min() - timedelta(days=10)
        max_date = trades['transaction_date'].max() + timedelta(days=10)
        
        # Download all tickers at once (much faster!)
        try:
            all_prices = yf.download(
                unique_tickers,
                start=min_date,
                end=max_date,
                progress=True,  # Show progress bar
                auto_adjust=True
            )
        except Exception as e:
            print(f"Error downloading batch prices: {e}")
            return pd.DataFrame()
        
        if all_prices.empty:
            print("No price data downloaded")
            return pd.DataFrame()
        
        # Estimate shares for each trade using the downloaded data
        trades['estimated_shares'] = trades.apply(
            lambda row: self._estimate_shares_from_batch(row, all_prices),
            axis=1
        )
        
        # Rest of the portfolio reconstruction...
        trades = trades.dropna(subset=['estimated_shares'])
        
        if len(trades) == 0:
            print("Could not estimate shares for any trades")
            return pd.DataFrame()
        
        # Track portfolio changes
        portfolio = {}
        portfolio_snapshots = []
        
        for _, trade in trades.iterrows():
            ticker = trade['ticker']
            shares = trade['estimated_shares']
            
            if trade['transaction_type'] == 'Purchase':
                portfolio[ticker] = portfolio.get(ticker, 0) + shares
            elif 'Sale' in str(trade['transaction_type']):
                current_shares = portfolio.get(ticker, 0)
                if 'Full' in str(trade['transaction_type']):
                    portfolio[ticker] = 0
                else:
                    portfolio[ticker] = max(0, current_shares - shares)
            
            portfolio = {k: v for k, v in portfolio.items() if v > 0}
            
            portfolio_snapshots.append({
                'date': trade['transaction_date'],
                'ticker': ticker,
                'action': trade['transaction_type'],
                'shares_changed': shares,
                'portfolio': portfolio.copy()
            })
        
        return pd.DataFrame(portfolio_snapshots)

    def _estimate_shares_from_batch(self, row, all_prices):
        """Estimate shares using pre-downloaded batch data"""
        ticker = row['ticker']
        date = row['transaction_date']
        
        try:
            # Access the close price for this ticker on the closest date
            if isinstance(all_prices.columns, pd.MultiIndex):
                # MultiIndex: ('Close', 'AAPL')
                if ('Close', ticker) not in all_prices.columns:
                    return None
                prices = all_prices[('Close', ticker)].dropna()
            else:
                # Single ticker or single-level columns
                if 'Close' not in all_prices.columns:
                    return None
                prices = all_prices['Close'].dropna()
            
            if len(prices) == 0:
                return None
            
            # Find closest date
            closest_idx = (prices.index - date).abs().argmin()
            price = float(prices.iloc[closest_idx])
            
            if pd.isna(price) or price <= 0:
                return None
            
            # Estimate amount
            amount = self._estimate_amount(
                row['amount_range_low'],
                row['amount_range_high']
            )
            
            if amount is None or amount <= 0:
                return None
            
            return amount / price
            
        except Exception as e:
            return None

# Usage example:
if __name__ == "__main__":
    congress_db = CongressDataBase()
    analyzer = CongressTradeAnalyzer(congress_db)
    
    # First, explore what data we have
    print("=" * 50)
    print("EXPLORING TRANSACTION TYPES")
    print("=" * 50)
    analyzer.explore_transaction_types()
    
    print("\n" + "=" * 50)
    print("LAG ANALYSIS")
    print("=" * 50)
    suspicious_trades = analyzer.calculate_lag_analysis()
    if len(suspicious_trades) > 0:
        print(f"Found {len(suspicious_trades)} suspicious trades based on filing delay and returns:")
        print(suspicious_trades[['ticker', 'transaction_date', 'days_to_file', 'ret_since']].head())
    else:
        print("No suspicious trades found")
    
    print("\n" + "=" * 50)
    print("PORTFOLIO RECONSTRUCTION")
    print("=" * 50)
    # Test with a known active trader
    test_filer = "house_rogerw_marshall"  # or find one with trades
    portfolio_df = analyzer.portfolio_reconstruction(test_filer)
    if len(portfolio_df) > 0:
        print(f"Portfolio reconstruction for {test_filer}:")
        print(portfolio_df[['date', 'ticker', 'action', 'shares_changed']].head(10))
    else:
        # Try to find a filer with trades
        query = """
            SELECT filer_id, COUNT(*) as trade_count
            FROM raw_trade
            WHERE ticker IS NOT NULL
            GROUP BY filer_id
            ORDER BY trade_count DESC
            LIMIT 5
        """
        top_filers = congress_db.sql_to_pandas(query)
        print("Top traders by number of trades:")
        print(top_filers)
        
        if len(top_filers) > 0:
            test_filer = top_filers.iloc[0]['filer_id']
            print(f"\nTrying {test_filer} instead...")
            portfolio_df = analyzer.portfolio_reconstruction(test_filer)
    
    print("\n" + "=" * 50)
    print("SELL TIMING ANALYSIS")
    print("=" * 50)
    good_sells = analyzer.analyze_sell_timing()
    if len(good_sells) > 0:
        print("Best-timed sells:")
        print(good_sells[['ticker', 'transaction_date', 'ret_30d']].head(10))
    
    print("\n" + "=" * 50)
    print("BET SIZE VS PERFORMANCE")
    print("=" * 50)
    bet_size_perf = analyzer.bet_size_vs_performance()
    if len(bet_size_perf) > 0:
        print("Bet size vs performance:")
        print(bet_size_perf)