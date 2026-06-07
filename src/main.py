from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from rakuten_api import RakutenApiClient
from scoring import filter_and_score_products
from sheets import SheetsClient

JST = ZoneInfo("Asia/Tokyo")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logger = logging.getLogger("rakuten-room-agent")

    try:
        application_id = get_required_env("RAKUTEN_APPLICATION_ID")
        service_account_json = get_required_env("GOOGLE_SERVICE_ACCOUNT_JSON")
        spreadsheet_id = get_required_env("SPREADSHEET_ID")
        sheet_name = os.getenv("SHEET_NAME", "Sheet1")

        today = datetime.now(JST).date()
        logger.info("楽天ROOM商品選定を開始します date=%s sheet=%s", today, sheet_name)

        rakuten_client = RakutenApiClient(application_id)
        products = rakuten_client.fetch_products()
        logger.info("楽天APIから候補商品を取得しました count=%s", len(products))

        sheets_client = SheetsClient(spreadsheet_id, service_account_json)
        sheets_client.ensure_headers(sheet_name)
        recent_urls = sheets_client.read_recent_urls(sheet_name, today=today, days=30)
        logger.info("過去30日以内の出力済みURLを読み込みました count=%s", len(recent_urls))

        deduped_products = [product for product in products if product.url not in recent_urls]
        logger.info("重複除外後の商品数 count=%s", len(deduped_products))

        selected_products = filter_and_score_products(deduped_products, today)
        logger.info("追記対象の商品数 count=%s", len(selected_products))

        sheets_client.append_products(sheet_name, selected_products, today=today)
        logger.info("Googleスプレッドシートへの追記が完了しました。")
        return 0
    except Exception:
        logger.exception("処理中にエラーが発生しました。")
        return 1


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"必須の環境変数が設定されていません: {name}")
    return value


if __name__ == "__main__":
    sys.exit(main())
