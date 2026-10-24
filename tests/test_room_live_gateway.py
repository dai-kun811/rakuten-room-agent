from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_live_gateway import RoomLiveGateway, classify_room_error, normalize_visible_text
from room_orchestrator import AuthenticationRequired, Confirmation
from room_poster import RoomPostError, RoomPostResult


class FakePoster:
    def __init__(self, result=None) -> None:
        self.calls = 0
        self.result = result

    def post(self, product_url, comment, *, before_submit=None, after_submit=None):
        self.calls += 1
        before_submit()
        after_submit()
        return self.result


class RoomLiveGatewayTests(unittest.TestCase):
    def candidate(self) -> dict:
        return {
            "normalized_url": "https://item.rakuten.co.jp/shop/item",
            "body": "十分な長さがある固有の投稿本文です。商品の確認点も自然に案内します。",
            "hashtags": ["#育児"],
        }

    def test_only_live_room_presence_confirms_posted(self) -> None:
        events = []
        gateway = RoomLiveGateway(FakePoster(), lambda body: body.startswith("十分"))
        result = gateway.submit(
            self.candidate(),
            before_submit=lambda: events.append("before"),
            after_submit=lambda: events.append("after"),
        )
        self.assertEqual(result, Confirmation.PRESENT)
        self.assertEqual(events, ["before", "after"])

    def test_missing_live_evidence_is_unknown_never_absent(self) -> None:
        gateway = RoomLiveGateway(FakePoster(), lambda _body: False)
        result = gateway.submit(
            self.candidate(), before_submit=lambda: None, after_submit=lambda: None
        )
        self.assertEqual(result, Confirmation.UNKNOWN)

    def test_login_error_is_classified_for_human_stop(self) -> None:
        result = classify_room_error(RoomPostError("楽天ROOMのログイン状態が期限切れです。"))
        self.assertIsInstance(result, AuthenticationRequired)

    def test_visible_text_normalization_ignores_layout_whitespace(self) -> None:
        self.assertEqual(normalize_visible_text(" 本文\n です "), "本文です")

    def test_verifier_receives_exact_comment_including_hashtags(self) -> None:
        observed = []
        gateway = RoomLiveGateway(FakePoster(), lambda comment: observed.append(comment) or True)
        result = gateway.submit(
            self.candidate(), before_submit=lambda: None, after_submit=lambda: None
        )
        self.assertEqual(result, Confirmation.PRESENT)
        self.assertEqual(
            observed,
            ["十分な長さがある固有の投稿本文です。商品の確認点も自然に案内します。\n\n#育児"],
        )

    def test_authenticated_api_success_is_definitive_without_feed_delay(self) -> None:
        verifier_calls = []
        poster = FakePoster(
            RoomPostResult(
                product_url="https://item.rakuten.co.jp/shop/item",
                status="posted",
                detail="api_collect_success",
            )
        )
        gateway = RoomLiveGateway(poster, lambda comment: verifier_calls.append(comment) or False)
        result = gateway.submit(
            self.candidate(), before_submit=lambda: None, after_submit=lambda: None
        )
        self.assertEqual(result, Confirmation.PRESENT)
        self.assertEqual(verifier_calls, [])

    def test_ambiguous_closed_window_still_requires_live_verifier(self) -> None:
        poster = FakePoster(
            RoomPostResult(
                product_url="https://item.rakuten.co.jp/shop/item",
                status="posted",
                detail="post_window_closed",
            )
        )
        gateway = RoomLiveGateway(poster, lambda _comment: False)
        result = gateway.submit(
            self.candidate(), before_submit=lambda: None, after_submit=lambda: None
        )
        self.assertEqual(result, Confirmation.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
