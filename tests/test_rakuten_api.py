from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rakuten_api import LATEST_ITEM_SEARCH_URL, LEGACY_ITEM_SEARCH_URL, RakutenApiClient


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.text = ""

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, *, params: dict, timeout: int) -> FakeResponse:
        self.calls.append((url, params))
        return FakeResponse(self.payload)


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
        self.assertNotIn("accessKey", session.calls[0][1])
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
        self.assertEqual(session.calls[0][1]["formatVersion"], 2)
        self.assertEqual(products[0].url, "https://example.com/latest")


if __name__ == "__main__":
    unittest.main()
