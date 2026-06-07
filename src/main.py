from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from rakuten_api import RakutenApiClient
from scoring import build_selection_tiers_from_env, count_filter_results, score_all_products, select_products
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
        access_key = os.getenv("RAKUTEN_ACCESS_KEY")
        referer = os.getenv("RAKUTEN_REFERER")
        service_account_json = get_required_env("GOOGLE_SERVICE_ACCOUNT_JSON")
        spreadsheet_id = get_required_env("SPREADSHEET_ID")
        sheet_name = os.getenv("SHEET_NAME", "Sheet1")

        today = datetime.now(JST).date()
        logger.info("楽天ROOM商品選定を開始します date=%s sheet=%s", today, sheet_name)

        sheets_client = SheetsClient(spreadsheet_id, service_account_json)
        sheets_client.ensure_headers(sheet_name)

        rakuten_client = RakutenApiClient(
            application_id,
            access_key=access_key,
            referer=referer,
        )
        products, fetch_report = rakuten_client.fetch_products()
        logger.info(
            "楽天API取得完了 unique_products=%s total_api_items=%s attempts=%s failures=%s",
            len(products),
            fetch_report.total_items,
            len(fetch_report.attempts),
            len(fetch_report.failed_attempts),
        )

        if not products:
            reason = fetch_report.failure_summary()
            logger.error("楽天APIから商品を取得できませんでした reason=%s", reason)
            sheets_client.append_error(sheet_name, today=today, reason=reason)
            return 0

        recent_urls = sheets_client.read_recent_urls(sheet_name, today=today, days=30)
        logger.info("過去30日以内の出力済みURLを読み込みました count=%s", len(recent_urls))

        deduped_products = [product for product in products if product.url not in recent_urls]
        logger.info("重複除外後の商品数 count=%s", len(deduped_products))

        if not deduped_products:
            reason = "楽天APIから商品は取得できましたが、過去30日以内に出力済みの商品URLとすべて重複しました。"
            logger.error(reason)
            sheets_client.append_error(sheet_name, today=today, reason=reason)
            return 0

        tiers = build_selection_tiers_from_env()
        filter_counts = count_filter_results(deduped_products, tiers)
        logger.info("選定条件別の候補数 counts=%s", filter_counts)

        selected_products = select_products(deduped_products, today, tiers)
        if not selected_products:
            top_products = score_all_products(deduped_products, today)[:3]
            details = [
                (
                    f"{item.product.name[:40]}..."
                    f" reviews={item.product.review_count}"
                    f" rating={item.product.review_average}"
                    f" score={item.total_score}"
                )
                for item in top_products
            ]
            reason = (
                "楽天APIから商品は取得できましたが、段階的な緩和条件でも選定対象が0件でした。"
                f"条件別候補数={filter_counts} 上位候補={details}"
            )
            logger.error(reason)
            sheets_client.append_error(sheet_name, today=today, reason=reason)
            return 0

        logger.info(
            "追記対象の商品数 count=%s tiers=%s",
            len(selected_products),
            [item.selection_tier for item in selected_products],
        )

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
