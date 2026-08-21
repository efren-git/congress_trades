import sqlite3
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, List

class CongressDataBase:
    """Creates the functions in order to get everything into SQL"""

    def __init__(self, db_path: str = "congress_trades.db"):
        self.db_path = db_path
        self.create_tables()

    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def create_tables(self):
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_trade (
                id TEXT UNIQUE NOT NULL PRIMARY KEY,
                filing_id TEXT,
                filer_id TEXT,
                source_id TEXT,
                transaction_date TIMESTAMP,
                notification_date TIMESTAMP,
                filing_date TIMESTAMP,
                owner TEXT,
                ticker TEXT,
                asset_name TEXT,
                asset_type TEXT,
                transaction_type TEXT,
                amount_range_low REAL,
                amount_range_high REAL,
                amount_range_label TEXT,
                comment TEXT,
                is_late INTEGER DEFAULT 0 CHECK(is_late IN (0, 1)),
                days_to_file REAL DEFAULT 0.0 CHECK(days_to_file >= 0),
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                row_index TEXT,
                notification_received_over_30d TEXT,
                doc_url TEXT,
                filing_type TEXT,
                ret_since REAL,
                excess_since REAL,
                ret_30d REAL,
                ret_1y REAL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_filers (
                id TEXT UNIQUE NOT NULL PRIMARY KEY,
                full_name TEXT,
                branch TEXT,
                chamber TEXT,
                party TEXT,
                state TEXT,
                agency TEXT,
                level TEXT,
                office TEXT,
                photo_url TEXT,
                trade_count INTEGER,
                purchases INTEGER,
                sales INTEGER,
                late_filings INTEGER,
                est_volume REAL
            )
        """)

        conn.commit()
        conn.close()

    def pandas_to_sql(self, df: pd.DataFrame, table_name: str):
        conn = self.get_connection()
        df.to_sql(table_name, conn, if_exists='replace', index=False)
        conn.close()
    
    def sql_to_pandas(self, query: str) -> pd.DataFrame:
        conn = self.get_connection()
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df


    def insert_trades(self, trades: pd.DataFrame):
        columns = [
            "id", "filing_id", "filer_id", "source_id", "transaction_date",
            "notification_date", "filing_date", "owner", "ticker", "asset_name",
            "asset_type", "transaction_type", "amount_range_low",
            "amount_range_high", "amount_range_label", "comment",
            "is_late", "days_to_file", "row_index",
            "notification_received_over_30d", "doc_url",
            "filing_type", "ret_since", "excess_since",
            "ret_30d", "ret_1y"
        ]
        
        # Debug: print column mapping
        print("DataFrame columns:", trades.columns.tolist())
        print("Expected columns:", columns)
        
        trades["notification_date"] = None
        trades["ingested_at"] = None
        trades["filing_id"] = trades["id"].str.replace(r'_[a-zA-Z]+\d+$', '', regex=True)
        # Validate that all expected columns exist
        missing_cols = [col for col in columns if col not in trades.columns]
        if missing_cols:
            raise ValueError(f"Missing columns in DataFrame: {missing_cols}")
        
        placeholders = ", ".join(["?"] * len(columns))
        column_list_sql = ", ".join(columns)
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Ensure exact column order
        trades = trades[columns].copy()
        
        # Convert all NaN to None for SQLite
        trades = trades.where(pd.notnull(trades), None)
        
        inserted_count = 0
        skipped_count = 0

        for _, row in trades.iterrows():
            #trades.where(pd.notnull(trades),None) already makes it None, why put None again?
            values = [None if pd.isna(row[col]) else row[col] for col in columns]
            try:
                #how does ignore work?
                cursor.execute(f"""
                    INSERT OR IGNORE INTO raw_trade ({column_list_sql})
                    VALUES ({placeholders})
                """, values)
                inserted_count += 1
            except sqlite3.IntegrityError as exc:
                skipped_count += 1
                print(f"Skipped trade {row.get('id')} due to: {exc}")
        
        conn.commit()
        conn.close()
        print(f"Inserted {inserted_count} trades; skipped {skipped_count} rows")

    def insert_filers(self, filers: pd.DataFrame):
        conn = self.get_connection()
        cursor = conn.cursor()

        """Insert multiple filers into the raw_filers table."""
        for _, filer in filers.iterrows():
            cursor.execute("""
                INSERT OR IGNORE INTO raw_filers (
                    id, full_name, branch, chamber, party,
                    state, agency, level, office, photo_url, trade_count, purchases, sales, late_filings, est_volume
                ) VALUES (?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuple(filer))

        conn.commit()
        conn.close()

    def has_filer(self, filer_id: str) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM raw_filers WHERE id = ?", (filer_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists


    def delete_table(self, table_name: str):
        """Delete a table from the local database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.commit()
        conn.close()


        