from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

LEGACY_ITEM_SEARCH_URL = (
    "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706"
)
LATEST_ITEM_SEARCH_URL = (
    "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401"
)
DEFAULT_HITS = 30
DEFAULT_CATEGORY_LIMIT = 5
DEFAULT_KEYWORDS_PER_CATEGORY = 1
DEFAULT_PAGES_PER_KEYWORD = 1
REQUEST_INTERVAL_SECONDS = 2.0
RATE_LIMIT_RETRY_SECONDS = 3.0
MAX_RATE_LIMIT_RETRIES = 3


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
        referer: str | None = None,
        session: Any | None = None,
        request_interval_seconds: float = REQUEST_INTERVAL_SECONDS,
        retry_sleep_seconds: float = RATE_LIMIT_RETRY_SECONDS,
    ) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.application_id = application_id
        self.access_key = access_key
        self.referer = self._normalize_referer(referer)
        self.session = session
        self.request_interval_seconds = request_interval_seconds
        self.retry_sleep_seconds = retry_sleep_seconds
        self.endpoint_url = LATEST_ITEM_SEARCH_URL if access_key else LEGACY_ITEM_SEARCH_URL
        self.endpoint_version = "20260401" if access_key else "20170706"
        self._last_endpoint_version = self.endpoint_version
        if not access_key:
            LOGGER.warning(
                "RAKUTEN_ACCESS_KEY が未設定のため旧版の楽天商品検索APIを使用します。"
                "最新版APIを使う場合はGitHub Secretsへ RAKUTEN_ACCESS_KEY を追加してください。"
            )
        if not self.referer:
            LOGGER.warning(
                "RAKUTEN_REFERER が未設定です。楽天アプリ設定でReferer制限がある場合は403になります。"
            )
        else:
            LOGGER.info(
                "楽天API Refererヘッダーを設定します header_names=%s masked_headers=%s",
                ["Referer"],
                self._masked_headers({"Referer": self.referer}),
            )

    def fetch_products(
        self,
        *,
        category_limit: int = DEFAULT_CATEGORY_LIMIT,
        keywords_per_category: int = DEFAULT_KEYWORDS_PER_CATEGORY,
        pages_per_keyword: int = DEFAULT_PAGES_PER_KEYWORD,
    ) -> tuple[list[Product], FetchReport]:
        products_by_url: dict[str, Product] = {}
        report = FetchReport()
        for category, keywords in list(CATEGORY_KEYWORDS.items())[:category_limit]:
            for keyword in keywords[:keywords_per_category]:
                for page in range(1, pages_per_keyword + 1):
                    try:
                        products = list(self._search(category, keyword, page))
                        report.attempts.append(
                            QueryAttempt(
                                category=category,
                                keyword=keyword,
                                page=page,
                                endpoint_version=self._last_endpoint_version,
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
                    except Exception as exc:
                        response = getattr(exc, "response", None)
                        status_code = (
                            response.status_code if response is not None else None
                        )
                        error = (
                            self._safe_error_text(response)
                            if response is not None
                            else str(exc)
                        )
                        report.attempts.append(
                            QueryAttempt(
                                category=category,
                                keyword=keyword,
                                page=page,
                                endpoint_version=self._last_endpoint_version,
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
                            self._last_endpoint_version,
                            status_code if status_code is not None else "unknown",
                            error,
                        )
                        if status_code == 403:
                            return list(products_by_url.values()), report
                    time.sleep(self.request_interval_seconds)
        return list(products_by_url.values()), report

    def _search(self, category: str, keyword: str, page: int) -> Iterable[Product]:
        # Keep the request intentionally minimal. Product quality filters are
        # applied after API retrieval to avoid wrong_parameter failures.
        params = {
            "applicationId": self.application_id,
            "keyword": keyword,
            "hits": DEFAULT_HITS,
            "page": page,
            "format": "json",
        }

        response = self._get_with_retries(params, self.endpoint_url, self.endpoint_version)
        if response.status_code == 403 and self.access_key:
            error = self._safe_error_text(response)
            if "REFERRER" in error.upper() or "REFERER" in error.upper():
                LOGGER.warning(
                    "最新版楽天APIでRefererエラーが発生したため旧APIへフォールバックします。"
                )
                legacy_params = dict(params)
                legacy_params.pop("accessKey", None)
                response = self._get_with_retries(
                    legacy_params,
                    LEGACY_ITEM_SEARCH_URL,
                    "20170706",
                )
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

    def _get_with_retries(
        self,
        params: dict[str, object],
        endpoint_url: str,
        endpoint_version: str,
    ) -> Any:
        headers = self._build_headers(endpoint_version)
        self._last_endpoint_version = endpoint_version
        LOGGER.info(
            "楽天API送信予定ヘッダー endpoint=%s header_names=%s masked_headers=%s",
            endpoint_version,
            sorted(headers),
            self._masked_headers(headers),
        )
        for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 2):
            response = self.session.get(
                endpoint_url,
                params=params,
                headers=headers,
                timeout=30,
            )
            self._log_sent_headers(response, endpoint_version)
            if response.status_code == 403:
                return response
            if response.status_code != 429:
                return response
            if attempt > MAX_RATE_LIMIT_RETRIES:
                return response
            LOGGER.warning(
                "楽天APIのレート制限を検知しました。%s秒待って再試行します attempt=%s/%s",
                int(self.retry_sleep_seconds),
                attempt,
                MAX_RATE_LIMIT_RETRIES,
            )
            time.sleep(self.retry_sleep_seconds)
        return response

    def _build_headers(self, endpoint_version: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.referer:
            headers["Referer"] = self.referer
        origin = self._origin_from_referer()
        if origin:
            headers["Origin"] = origin
        if self.access_key and endpoint_version == "20260401":
            headers["accessKey"] = self.access_key
        return headers

    def _log_sent_headers(self, response: Any, endpoint_version: str) -> None:
        request = getattr(response, "request", None)
        sent_headers = getattr(request, "headers", None)
        if not sent_headers:
            LOGGER.info("楽天API実送信ヘッダーはレスポンスから確認できませんでした endpoint=%s", endpoint_version)
            return
        headers = dict(sent_headers)
        LOGGER.info(
            "楽天API実送信ヘッダー endpoint=%s header_names=%s masked_headers=%s",
            endpoint_version,
            sorted(headers),
            self._masked_headers(headers),
        )

    def _masked_headers(self, headers: dict[str, object] | None) -> dict[str, str]:
        if not headers:
            return {}
        return {str(name): "***" for name in headers}

    def _normalize_referer(self, referer: str | None) -> str | None:
        if not referer:
            return None
        normalized = referer.strip()
        if not normalized:
            return None
        if not normalized.startswith(("http://", "https://")):
            normalized = f"https://{normalized}"
        return normalized

    def _origin_from_referer(self) -> str | None:
        if not self.referer:
            return None
        parsed = urlparse(self.referer)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

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
            if isinstance(payload, dict):
                error = payload.get("error", "")
                description = payload.get("error_description", "")
                detail = " ".join(part for part in [error, description] if part)
                if response.status_code == 403:
                    detail = f"{detail} Referer設定を確認してください。"
                return (detail or str(payload))[:300]
            error = payload
            return str(error)[:300]
        except ValueError:
            return response.text[:300]
