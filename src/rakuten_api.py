from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

LOGGER = logging.getLogger(__name__)

LEGACY_ITEM_SEARCH_URL = (
    "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706"
)
LATEST_ITEM_SEARCH_URL = (
    "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401"
)
DEFAULT_GENRE_ID = "100533"


@dataclass(frozen=True)
class Product:
    category: str
    name: str
    url: str
    price: int
    review_count: int
    review_average: float
    caption: str
    catchcopy: str
    shop_name: str
    image_url: str

    @property
    def text(self) -> str:
        return " ".join(
            [self.category, self.name, self.caption, self.catchcopy, self.shop_name]
        ).lower()


@dataclass
class QueryAttempt:
    category: str
    keyword: str
    page: int
    endpoint_version: str
    status_code: int | None
    item_count: int
    error: str = ""


@dataclass
class FetchReport:
    attempts: list[QueryAttempt] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return sum(attempt.item_count for attempt in self.attempts)

    @property
    def failed_attempts(self) -> list[QueryAttempt]:
        return [attempt for attempt in self.attempts if attempt.error]

    @property
    def successful_attempts(self) -> list[QueryAttempt]:
        return [attempt for attempt in self.attempts if not attempt.error]

    def failure_summary(self) -> str:
        if not self.attempts:
            return "楽天APIへのリクエストが実行されませんでした。"
        if self.successful_attempts:
            return (
                f"楽天APIは応答しましたが、取得商品が0件でした。"
                f"成功クエリ数={len(self.successful_attempts)}、失敗クエリ数={len(self.failed_attempts)}。"
            )
        reasons = []
        for attempt in self.failed_attempts[:5]:
            status = attempt.status_code if attempt.status_code is not None else "no_status"
            reasons.append(
                f"{attempt.endpoint_version}/{attempt.keyword}/p{attempt.page}: status={status} {attempt.error}"
            )
        return "楽天API取得がすべて失敗しました。" + " / ".join(reasons)


CATEGORY_KEYWORDS = {
    "育児便利グッズ": ["育児 便利 グッズ", "育児 時短"],
    "ベビー用品": ["ベビー用品", "赤ちゃん グッズ"],
    "キッズ用品": ["キッズ用品", "子ども グッズ"],
    "知育玩具": ["知育玩具", "知育 おもちゃ"],
    "おうち遊び": ["おうち遊び 子ども", "室内遊び 子ども"],
    "外遊び": ["外遊び 子ども", "キッズ 外遊び"],
    "育児時短グッズ": ["育児 時短 グッズ", "子育て 時短"],
    "子ども靴": ["子ども 靴", "キッズ シューズ"],
    "絵本": ["絵本 子ども", "知育 絵本"],
    "育児家電": ["育児 家電", "ベビー 家電"],
    "プレゼント向け商品": ["子ども プレゼント", "ベビー ギフト"],
}


class RakutenApiClient:
    def __init__(
        self,
        application_id: str,
        *,
        access_key: str | None = None,
        genre_id: str = DEFAULT_GENRE_ID,
        session: Any | None = None,
    ) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.application_id = application_id
        self.access_key = access_key
        self.genre_id = genre_id
        self.session = session
        self.endpoint_url = LATEST_ITEM_SEARCH_URL if access_key else LEGACY_ITEM_SEARCH_URL
        self.endpoint_version = "20260401" if access_key else "20170706"
        if not access_key:
            LOGGER.warning(
                "RAKUTEN_ACCESS_KEY が未設定のため旧版の楽天商品検索APIを使用します。"
                "最新版APIを使う場合はGitHub Secretsへ RAKUTEN_ACCESS_KEY を追加してください。"
            )

    def fetch_products(self, *, pages_per_keyword: int = 2) -> tuple[list[Product], FetchReport]:
        import requests

        products_by_url: dict[str, Product] = {}
        report = FetchReport()
        for category, keywords in CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                for page in range(1, pages_per_keyword + 1):
                    try:
                        products = list(self._search(category, keyword, page))
                        report.attempts.append(
                            QueryAttempt(
                                category=category,
                                keyword=keyword,
                                page=page,
                                endpoint_version=self.endpoint_version,
                                status_code=200,
                                item_count=len(products),
                            )
                        )
                        LOGGER.info(
                            "楽天API取得 category=%s keyword=%s page=%s endpoint=%s items=%s",
                            category,
                            keyword,
                            page,
                            self.endpoint_version,
                            len(products),
                        )
                        for product in products:
                            products_by_url.setdefault(product.url, product)
                    except requests.HTTPError as exc:
                        status_code = exc.response.status_code if exc.response else None
                        error = self._safe_error_text(exc.response)
                        report.attempts.append(
                            QueryAttempt(
                                category=category,
                                keyword=keyword,
                                page=page,
                                endpoint_version=self.endpoint_version,
                                status_code=status_code,
                                item_count=0,
                                error=error,
                            )
                        )
                        LOGGER.warning(
                            "楽天API取得失敗 category=%s keyword=%s page=%s endpoint=%s status=%s error=%s",
                            category,
                            keyword,
                            page,
                            self.endpoint_version,
                            status_code if status_code is not None else "unknown",
                            error,
                        )
                    except requests.RequestException as exc:
                        report.attempts.append(
                            QueryAttempt(
                                category=category,
                                keyword=keyword,
                                page=page,
                                endpoint_version=self.endpoint_version,
                                status_code=None,
                                item_count=0,
                                error=str(exc),
                            )
                        )
                        LOGGER.warning(
                            "楽天API接続失敗 category=%s keyword=%s page=%s endpoint=%s error=%s",
                            category,
                            keyword,
                            page,
                            self.endpoint_version,
                            exc,
                        )
                    time.sleep(0.2)
        return list(products_by_url.values()), report

    def _search(self, category: str, keyword: str, page: int) -> Iterable[Product]:
        params = {
            "applicationId": self.application_id,
            "format": "json",
            "keyword": keyword,
            "genreId": self.genre_id,
            "hits": 30,
            "page": page,
            "sort": "-reviewCount",
            "availability": 1,
            "imageFlag": 1,
            "hasReviewFlag": 1,
            "orFlag": 1,
            "field": 0,
        }
        if self.access_key:
            params["accessKey"] = self.access_key
            params["formatVersion"] = 2

        response = self.session.get(self.endpoint_url, params=params, timeout=30)
        if response.status_code == 404:
            LOGGER.info("楽天APIに該当商品がありません category=%s keyword=%s page=%s", category, keyword, page)
            return []
        response.raise_for_status()
        payload = response.json()
        return [self._to_product(category, item) for item in self._extract_items(payload)]

    def _extract_items(self, payload: dict) -> list[dict]:
        if "items" in payload:
            return [item["item"] if "item" in item else item for item in payload.get("items", [])]
        if "Items" in payload:
            return [item["Item"] if "Item" in item else item for item in payload.get("Items", [])]
        return []

    def _to_product(self, category: str, item: dict) -> Product:
        image_url = ""
        image_urls = item.get("mediumImageUrls") or []
        if image_urls:
            image_url = str(image_urls[0].get("imageUrl", ""))

        return Product(
            category=category,
            name=str(item.get("itemName", "")),
            url=str(item.get("itemUrl", "")),
            price=int(item.get("itemPrice") or 0),
            review_count=int(item.get("reviewCount") or 0),
            review_average=float(item.get("reviewAverage") or 0),
            caption=str(item.get("itemCaption", "")),
            catchcopy=str(item.get("catchcopy", "")),
            shop_name=str(item.get("shopName", "")),
            image_url=image_url,
        )

    def _safe_error_text(self, response: Any | None) -> str:
        if response is None:
            return ""
        try:
            payload = response.json()
            error = payload.get("error") or payload.get("error_description") or payload
            return str(error)[:300]
        except ValueError:
            return response.text[:300]
