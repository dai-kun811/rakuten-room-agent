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
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise FakeHttpError(self)
        return None


class FakeSession:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, *, params: dict, timeout: int) -> FakeResponse:
        self.calls.append((url, params))
        return FakeResponse(self.payload, self.status_code)


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

        client = RakutenApiClient("secret-app-id", session=session)
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

        client = RakutenApiClient("secret-app-id", access_key="secret-access-key", session=session)
        products = list(client._search("知育玩具", "知育玩具", 1))

        self.assertEqual(session.calls[0][0], LATEST_ITEM_SEARCH_URL)
        self.assertEqual(session.calls[0][1]["accessKey"], "secret-access-key")
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
        client = RakutenApiClient("secret-app-id", session=session)

        _products, report = client.fetch_products(pages_per_keyword=1)

        self.assertEqual(report.failed_attempts[0].status_code, 400)
        self.assertIn("wrong_parameter", report.failed_attempts[0].error)


if __name__ == "__main__":
    unittest.main()
