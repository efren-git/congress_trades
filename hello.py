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
        # note: ingested_at deliberately excluded so SQLite's DEFAULT CURRENT_TIMESTAMP fills it in

placeholders = ", ".join(["?"] * len(columns))
column_list_sql = ", ".join(columns)

print(placeholders)
print(f"{column_list_sql}")