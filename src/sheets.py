from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from post_generator import build_hashtags, build_post_text
from scoring import ScoredProduct

LOGGER = logging.getLogger(__name__)

SHEET_HEADERS = [
    "日付",
    "カテゴリ",
    "商品名",
    "商品URL",
    "価格",
    "レビュー件数",
    "評価",
    "商品区分",
    "総合スコア",
    "おすすめ理由",
    "楽天ROOM投稿文",
    "おすすめハッシュタグ",
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self, spreadsheet_id: str, service_account_json: str) -> None:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        self.spreadsheet_id = spreadsheet_id
        credentials = Credentials.from_service_account_info(
            json.loads(service_account_json), scopes=SCOPES
        )
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def ensure_headers(self, sheet_name: str) -> None:
        values = self.read_values(f"{sheet_name}!A1:L1")
        if values:
            return
        self.append_rows(sheet_name, [SHEET_HEADERS])

    def read_recent_urls(self, sheet_name: str, *, today: date, days: int = 30) -> set[str]:
        values = self.read_values(f"{sheet_name}!A:L")
        if not values:
            return set()

        rows = values[1:] if values[0] == SHEET_HEADERS else values
        threshold = today - timedelta(days=days)
        recent_urls: set[str] = set()

        for row in rows:
            if len(row) < 4:
                continue
            row_date = parse_date(row[0])
            if row_date and row_date >= threshold and row[3]:
                recent_urls.add(row[3])
        return recent_urls

    def append_products(
        self,
        sheet_name: str,
        scored_products: list[ScoredProduct],
        *,
        today: date,
    ) -> None:
        if not scored_products:
            LOGGER.info("追記対象の商品がありません。")
            return
        rows = [scored_product_to_row(item, today=today) for item in scored_products]
        self.append_rows(sheet_name, rows)

    def append_error(self, sheet_name: str, *, today: date, reason: str) -> None:
        LOGGER.error("ERROR行をスプレッドシートへ追記します reason=%s", reason)
        self.append_rows(sheet_name, [error_row(today=today, reason=reason)])

    def append_rows(self, sheet_name: str, rows: list[list[object]]) -> None:
        (
            self.service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!A:L",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )

    def read_values(self, range_name: str) -> list[list[str]]:
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )
        return response.get("values", [])


def scored_product_to_row(item: ScoredProduct, *, today: date) -> list[object]:
    product = item.product
    return [
        today.isoformat(),
        product.category,
        product.name,
        product.url,
        product.price,
        product.review_count,
        product.review_average,
        item.product_rank,
        item.total_score,
        f"{item.recommendation_reason} 選定条件={item.selection_tier}",
        build_post_text(item),
        build_hashtags(product.category, item.product_rank),
    ]


def error_row(*, today: date, reason: str) -> list[object]:
    return [
        today.isoformat(),
        "ERROR",
        "商品データなし",
        "",
        "",
        "",
        "",
        "ERROR",
        "",
        reason[:1000],
        "GitHub Actionsのログを確認してください。",
        "",
    ]


def parse_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None
