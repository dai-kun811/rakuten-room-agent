from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from fixed_rule_generator import GeneratedPost
from scoring import ScoredProduct

LOGGER = logging.getLogger(__name__)
DEFAULT_REVIEW_SHEET_NAME = "ROOM_Posts_Review"
GOOGLE_READ_RETRIES = 3

SHEET_HEADERS = [
    "日付",
    "実行ID",
    "カテゴリ",
    "検索キーワード",
    "商品名",
    "商品URL",
    "正規化URL",
    "価格",
    "レビュー件数",
    "評価",
    "ショップ名",
    "商品区分",
    "商品タイプ",
    "訴求軸",
    "構成パターン",
    "想定ターゲット",
    "ターゲットの悩み",
    "検索意図",
    "購入前不安",
    "使用シーン",
    "ベネフィット",
    "購入前確認点",
    "選定理由",
    "総合スコア",
    "需要スコア",
    "投稿品質スコア",
    "リライト回数",
    "ステータス",
    "タイトル",
    "投稿文",
    "ハッシュタグ",
    "改善コメント",
    "画像URL",
    "生成モード",
]

LEGACY_HEADERS = [
    "日付",
    "カテゴリ",
    "商品名",
    "商品URL",
    "価格",
    "レビュー件数",
    "評価",
    "商品区分",
    "訴求カテゴリ",
    "ベネフィット",
    "購入前確認点",
    "総合スコア",
    "おすすめ理由",
    "楽天ROOM投稿文",
    "おすすめハッシュタグ",
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def target_sheet_for_status(
    status: str,
    *,
    output_sheet_name: str,
    review_sheet_name: str,
) -> str:
    return output_sheet_name if status == "ready" else review_sheet_name


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
        self.ensure_sheet_exists(sheet_name)
        values = self.read_values(f"{sheet_name}!A1:AH1")
        if values and values[0] == SHEET_HEADERS:
            return
        if values and any(cell for cell in values[0]):
            raise RuntimeError(
                f"出力先シート {sheet_name} には旧形式または別形式のヘッダーがあります。"
                "既存データを保護するため上書きしません。OUTPUT_SHEET_NAME に新しいシート名を指定してください。"
            )
        self.update_row(f"{sheet_name}!A1:AH1", SHEET_HEADERS)

    def ensure_sheet_exists(self, sheet_name: str) -> None:
        metadata = (
            self.service.spreadsheets()
            .get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title")
            .execute(num_retries=GOOGLE_READ_RETRIES)
        )
        titles = {
            sheet.get("properties", {}).get("title", "")
            for sheet in metadata.get("sheets", [])
        }
        if sheet_name in titles:
            return
        (
            self.service.spreadsheets()
            .batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            )
            .execute()
        )

    def read_existing_urls(self, sheet_name: str) -> set[str]:
        values = self.read_values(f"{sheet_name}!A:AH")
        if not values:
            return set()
        headers = values[0]
        url_index = header_index(headers, "正規化URL")
        if url_index is None:
            url_index = header_index(headers, "商品URL")
        if url_index is None:
            return set()
        existing_urls: set[str] = set()
        for row in values[1:]:
            if len(row) <= url_index:
                continue
            value = str(row[url_index]).strip()
            if value.startswith("http"):
                existing_urls.add(normalize_product_url(value))
        return existing_urls

    def read_recent_history(self, sheet_name: str, *, today: date, days: int = 30) -> list[dict[str, str]]:
        values = self.read_values(f"{sheet_name}!A:AH")
        if not values:
            return []
        headers = values[0]
        cutoff = today - timedelta(days=days)
        history: list[dict[str, str]] = []
        for row in values[1:]:
            record = {
                header: str(row[index]) if index < len(row) else ""
                for index, header in enumerate(headers)
            }
            row_date = parse_date(record.get("日付", ""))
            if row_date is not None and row_date >= cutoff:
                history.append(record)
        return history

    def append_products(
        self,
        sheet_name: str,
        rows: list[list[object]],
    ) -> None:
        if not rows:
            LOGGER.info("追記対象の商品がありません。")
            return
        self.append_rows(sheet_name, rows)

    def append_error(
        self,
        sheet_name: str,
        *,
        today: date,
        run_id: str,
        reason: str,
    ) -> None:
        LOGGER.error("ERROR行をスプレッドシートへ追記します reason=%s", reason)
        self.append_rows(
            sheet_name,
            [error_row(today=today, run_id=run_id, reason=reason)],
        )

    def append_rows(self, sheet_name: str, rows: list[list[object]]) -> None:
        (
            self.service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{sheet_name}!A:AH",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )

    def update_row(self, range_name: str, row: list[object]) -> None:
        (
            self.service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            )
            .execute()
        )

    def read_values(self, range_name: str) -> list[list[str]]:
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute(num_retries=GOOGLE_READ_RETRIES)
        )
        return response.get("values", [])

def scored_product_to_row(
    item: ScoredProduct,
    generated: GeneratedPost,
    *,
    today: date,
    run_id: str,
) -> list[object]:
    product = item.product
    analysis = generated.analysis
    product_type = analysis.product_type
    return [
        today.isoformat(),
        run_id,
        product.category,
        product.search_keyword,
        product.name,
        product.url,
        normalize_product_url(product.url),
        product.price,
        product.review_count,
        product.review_average,
        product.shop_name,
        item.product_rank,
        product_type,
        analysis.appeal_axis,
        generated.structure_pattern,
        analysis.target,
        analysis.user_pain,
        analysis.search_intent,
        analysis.purchase_anxiety,
        analysis.usage_scene,
        analysis.benefit,
        "・".join(generated.attributes.purchase_checkpoints) if generated.attributes else "",
        generated.recommendation_reason,
        item.total_score,
        item.demand_score,
        generated.quality.score,
        generated.rewrite_count,
        generated.status,
        generated.title,
        generated.body,
        " ".join(generated.hashtags),
        " / ".join(generated.quality_errors),
        product.image_url,
        generated.generation_mode,
    ]


def error_row(*, today: date, run_id: str, reason: str) -> list[object]:
    row: list[object] = [""] * len(SHEET_HEADERS)
    row[0] = today.isoformat()
    row[1] = run_id
    row[2] = "ERROR"
    row[22] = reason[:1000]
    row[27] = "ERROR"
    row[31] = "GitHub Actionsのログを確認してください。"
    row[33] = "system"
    return row


def header_index(headers: list[str], name: str) -> int | None:
    try:
        return headers.index(name)
    except ValueError:
        return None


def parse_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def normalize_product_url(url: str) -> str:
    return url.strip().split("?")[0].rstrip("/")
