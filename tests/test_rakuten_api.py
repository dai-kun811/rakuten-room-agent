from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rakuten_api import LATEST_ITEM_SEARCH_URL, LEGACY_ITEM_SEARCH_URL, RakutenApiClient


class FakeHttpError(Exception):
    def __init__(self, response: "FakeResponse") -> None:
        super().__init__("fake http error")
        self.response = response


class FakeResponse:
    def __init__(
        self,
        payload: dict,
        status_code: int = 200,
        request_headers: dict | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = ""
        self.request = type("FakeRequest", (), {"headers": request_headers or {}})()

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise FakeHttpError(self)
        return None


class FakeSession:
    def __init__(
        self,
        payload: dict,
        status_code: int = 200,
        status_codes: list[int] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.status_codes = status_codes or []
        self.calls: list[tuple[str, dict, dict | None]] = []

    def get(
        self,
        url: str,
        *,
        params: dict,
        headers: dict | None = None,
        timeout: int,
    ) -> FakeResponse:
        self.calls.append((url, params, headers))
        status_code = self.status_codes.pop(0) if self.status_codes else self.status_code
        return FakeResponse(self.payload, status_code, request_headers=headers)


class RakutenApiTest(unittest.TestCase):
    def test_legacy_endpoint_parses_wrapped_items(self) -> None:
        session = FakeSession(
            {
                "Items": [
                    {
                        "Item": {
                            "itemName": "ベビー用品",
                            "itemUrl": "https://example.com/legacy",
                            "itemPrice": 1000,
                            "reviewCount": 120,
                            "reviewAverage": 4.4,
                        }
                    }
                ]
            }
        )

        client = RakutenApiClient(
            "secret-app-id",
            session=session,
            request_interval_seconds=0,
            retry_sleep_seconds=0,
        )
        products = list(client._search("ベビー用品", "ベビー用品", 1))

        self.assertEqual(session.calls[0][0], LEGACY_ITEM_SEARCH_URL)
        self.assertEqual(
            set(session.calls[0][1]),
            {"applicationId", "keyword", "hits", "page", "format"},
        )
        self.assertEqual(products[0].url, "https://example.com/legacy")

    def test_latest_endpoint_uses_access_key_and_flat_items(self) -> None:
        session = FakeSession(
            {
                "items": [
                    {
                        "itemName": "知育玩具",
                        "itemUrl": "https://example.com/latest",
                        "itemPrice": 2000,
                        "reviewCount": 300,
                        "reviewAverage": 4.6,
                    }
                ]
            }
        )

        client = RakutenApiClient(
            "secret-app-id",
            access_key="secret-access-key",
            referer="https://github.com/dai-kun811/rakuten-room-agent",
            session=session,
            request_interval_seconds=0,
            retry_sleep_seconds=0,
        )
        products = list(client._search("知育玩具", "知育玩具", 1))

        self.assertEqual(session.calls[0][0], LATEST_ITEM_SEARCH_URL)
        self.assertEqual(session.calls[0][1]["accessKey"], "secret-access-key")
        self.assertEqual(
            session.calls[0][2],
            {"Referer": "https://github.com/dai-kun811/rakuten-room-agent"},
        )
        self.assertEqual(
            client._masked_headers(session.calls[0][2]),
            {"Referer": "***"},
        )
        self.assertEqual(
            set(session.calls[0][1]),
            {"applicationId", "accessKey", "keyword", "hits", "page", "format"},
        )
        self.assertEqual(products[0].url, "https://example.com/latest")

    def test_failed_response_keeps_status_code_in_report(self) -> None:
        session = FakeSession(
            {
                "error": "wrong_parameter",
                "error_description": "keyword parameter is not valid",
            },
            status_code=400,
        )
        client = RakutenApiClient(
            "secret-app-id",
            session=session,
            request_interval_seconds=0,
            retry_sleep_seconds=0,
        )

        _products, report = client.fetch_products(pages_per_keyword=1)

        self.assertEqual(report.failed_attempts[0].status_code, 400)
        self.assertIn("wrong_parameter", report.failed_attempts[0].error)

    def test_fetch_products_limits_initial_request_count(self) -> None:
        session = FakeSession({"Items": []})
        client = RakutenApiClient(
            "secret-app-id",
            session=session,
            request_interval_seconds=0,
            retry_sleep_seconds=0,
        )

        _products, report = client.fetch_products()

        self.assertEqual(len(session.calls), 5)
        self.assertEqual(len(report.attempts), 5)

    def test_429_retries_three_times_then_succeeds(self) -> None:
        session = FakeSession(
            {
                "items": [
                    {
                        "itemName": "知育玩具",
                        "itemUrl": "https://example.com/rate-limit",
                    }
                ]
            },
            status_codes=[429, 429, 429, 200],
        )
        client = RakutenApiClient(
            "secret-app-id",
            access_key="secret-access-key",
            referer="https://github.com/dai-kun811/rakuten-room-agent",
            session=session,
            request_interval_seconds=0,
            retry_sleep_seconds=0,
        )

        products = list(client._search("知育玩具", "知育玩具", 1))

        self.assertEqual(len(session.calls), 4)
        self.assertEqual(products[0].url, "https://example.com/rate-limit")

    def test_403_does_not_retry_and_mentions_referer(self) -> None:
        session = FakeSession(
            {
                "error": "REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING",
                "error_description": "referer is missing",
            },
            status_code=403,
        )
        client = RakutenApiClient(
            "secret-app-id",
            access_key="secret-access-key",
            referer="https://github.com/dai-kun811/rakuten-room-agent",
            session=session,
            request_interval_seconds=0,
            retry_sleep_seconds=0,
        )

        _products, report = client.fetch_products(category_limit=1)

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(report.failed_attempts[0].status_code, 403)
        self.assertIn("Referer設定を確認", report.failed_attempts[0].error)

    def test_build_headers_returns_empty_dict_when_referer_missing(self) -> None:
        client = RakutenApiClient(
            "secret-app-id",
            session=FakeSession({"Items": []}),
            request_interval_seconds=0,
            retry_sleep_seconds=0,
        )

        self.assertEqual(client._build_headers(), {})


if __name__ == "__main__":
    unittest.main()
