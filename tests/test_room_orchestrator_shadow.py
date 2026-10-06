from __future__ import annotations

import ast
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from room_orchestrator_shadow import (
    ShadowAction,
    audit_shadow_manifests,
    evaluate_shadow_manifest,
)


JST = timezone(timedelta(hours=9))


def manifest(*, day: str = "2026-10-06") -> dict:
    slots = {}
    bodies = (
        "【ベビー保湿剤｜お風呂上がりの準備】着替えと片づけが重なる時間は、保湿用品を探すだけでも慌ただしいですよね。ポンプ式のベビー保湿剤なら、使う物を一つに決めて着替えのそばへ準備でき、片手がふさがる場面にも取り出しやすいです。成分・使える部位・容量を商品ページで確かめると、家族が交代しても同じ手順でケアしやすくなります。",
        "【スリーパー｜夜の着替え準備】季節の変わり目は、寝る前に何を着せるか毎晩迷いやすいですよね。ガーゼ素材のスリーパーなら、サイズ表と素材表示を見ながら今の体格に合う一枚か判断でき、寝具との組み合わせも考えやすいです。洗濯方法と対象サイズを家族で共有すると、夜の着替えで別の布ものを探し直す手間を減らせます。",
        "【積み木｜親子で音遊び】雨の日に家の中で過ごす時間が長いと、次に出すおもちゃを考える時間が増えますよね。音が鳴る木製積み木なら、振って音を聞く遊びと、積んだり並べたりする遊びを切り替えられ、親子で反応を見ながら遊べます。対象年齢・パーツサイズ・セット内容を確かめると、今の手先遊びに合うおもちゃか判断しやすくなります。",
    )
    for index, slot in enumerate(("morning", "noon", "evening"), start=1):
        url = f"https://item.rakuten.co.jp/shop/item-{index}"
        slots[slot] = {
            "status": "ready",
            "candidate": {
                "product_url": url,
                "normalized_url": url,
                "product_name": f"商品 {index}",
                "short_product_label": f"商品{index}",
                "product_type": "baby_care",
                "title": f"商品{index}｜毎日の準備",
                "body": bodies[index - 1],
                "hashtags": ["#ベビーケア", "#毎日の育児", "#赤ちゃんケア", "#育児ケア", "#とらパパ厳選"],
                "recommendation_reason": "毎日の準備に使える商品です。",
                "confirmed_features": [],
                "confirmed_use_cases": [],
                "purchase_checkpoints": ["サイズ", "素材", "使用方法"],
                "source_evidence": {"caption": f"商品 {index} の説明"},
                "quality": {"status": "passed", "errors": []},
                "content_hash": f"{index:064x}",
            },
        }
    return {
        "schema_version": 2,
        "routine_date_jst": day,
        "revision": 1,
        "supersedes_revision": None,
        "actions_run_id": "123",
        "head_sha": "abcdef1",
        "generated_at": f"{day}T07:00:00+09:00",
        "slots": slots,
    }


class RoomOrchestratorShadowTests(unittest.TestCase):
    def test_phase4_runner_contains_no_mutating_http_method(self) -> None:
        runner = Path(__file__).resolve().parents[1] / "scripts" / "run_phase4_shadow.py"
        tree = ast.parse(runner.read_text(encoding="utf-8"))
        methods = {
            node.func.attr.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue({"post", "put", "patch", "delete"}.isdisjoint(methods))

    def test_due_slot_would_claim_without_external_mutation(self) -> None:
        decisions = evaluate_shadow_manifest(
            manifest(),
            now=datetime(2026, 10, 6, 12, 0, tzinfo=JST),
        )
        self.assertEqual(
            [decision.action for decision in decisions],
            [ShadowAction.WOULD_CLAIM, ShadowAction.WOULD_CLAIM, ShadowAction.WAIT],
        )

    def test_blocked_slot_does_not_stop_later_ready_slot(self) -> None:
        value = manifest()
        value["slots"]["morning"] = {"status": "blocked", "reason": "quality_rejected"}
        decisions = evaluate_shadow_manifest(
            value,
            now=datetime(2026, 10, 6, 19, 0, tzinfo=JST),
        )
        self.assertEqual(decisions[0].action, ShadowAction.BLOCKED)
        self.assertEqual(decisions[1].action, ShadowAction.WOULD_CLAIM)
        self.assertEqual(decisions[2].action, ShadowAction.WOULD_CLAIM)

    def test_reserved_and_unknown_failure_require_human_hold(self) -> None:
        events = [
            {"post_slot": "2026-10-06:morning", "status": "reserved"},
            {"post_slot": "2026-10-06:noon", "status": "failed", "detail": "TimeoutError"},
        ]
        decisions = evaluate_shadow_manifest(
            manifest(),
            now=datetime(2026, 10, 6, 19, 0, tzinfo=JST),
            legacy_events=events,
        )
        self.assertEqual(decisions[0].action, ShadowAction.HUMAN_HOLD)
        self.assertEqual(decisions[1].action, ShadowAction.HUMAN_HOLD)
        self.assertEqual(decisions[2].action, ShadowAction.WOULD_CLAIM)

    def test_posted_url_is_never_claimed_again(self) -> None:
        value = manifest()
        url = value["slots"]["evening"]["candidate"]["product_url"]
        decisions = evaluate_shadow_manifest(
            value,
            now=datetime(2026, 10, 6, 19, 0, tzinfo=JST),
            posted_urls=[url + "?scid=test"],
        )
        self.assertEqual(decisions[2].action, ShadowAction.HUMAN_HOLD)
        self.assertEqual(decisions[2].reason, "product_url_already_posted")

    def test_past_unposted_day_expires_each_ready_slot(self) -> None:
        decisions = evaluate_shadow_manifest(
            manifest(day="2026-10-05"),
            now=datetime(2026, 10, 6, 8, 0, tzinfo=JST),
        )
        self.assertTrue(all(value.action is ShadowAction.EXPIRED_UNPOSTED for value in decisions))

    def test_quality_audit_rejects_cross_day_duplicate_and_similarity(self) -> None:
        first = manifest(day="2026-10-05")
        second = manifest(day="2026-10-06")
        second["slots"]["morning"]["candidate"] = deepcopy(first["slots"]["morning"]["candidate"])
        result = audit_shadow_manifests([first, second])
        self.assertFalse(result.passed)
        self.assertEqual(result.duplicate_url_count, 3)
        self.assertGreaterEqual(result.maximum_similarity, 0.75)

    def test_quality_audit_accepts_valid_distinct_candidates(self) -> None:
        value = manifest()
        result = audit_shadow_manifests([value])
        self.assertTrue(result.passed, result.issues)
        self.assertEqual(result.candidate_count, 3)

    def test_quality_audit_rejects_stale_unenriched_artifact(self) -> None:
        value = manifest()
        candidate = value["slots"]["morning"]["candidate"]
        candidate["recommendation_reason"] = ""
        candidate["purchase_checkpoints"] = []
        candidate["source_evidence"] = {}
        result = audit_shadow_manifests([value])
        self.assertFalse(result.passed)
        self.assertTrue(any("recommendation_reason_missing" in issue for issue in result.issues))
        self.assertTrue(any("purchase_checkpoints_missing" in issue for issue in result.issues))
        self.assertTrue(any("source_evidence_missing" in issue for issue in result.issues))

    def test_quality_audit_routes_self_feeding_support_to_human_review(self) -> None:
        value = manifest()
        candidate = value["slots"]["evening"]["candidate"]
        candidate["product_type"] = "nursing_support"
        candidate["product_name"] = "セルフミルク 哺乳瓶ホルダー 赤ちゃんが自分で飲む"
        result = audit_shadow_manifests([value])
        self.assertFalse(result.passed)
        self.assertTrue(
            any("human_review_required_feeding_support" in issue for issue in result.issues)
        )

    def test_decision_blocks_candidate_that_fails_shadow_quality_gate(self) -> None:
        value = manifest()
        value["slots"]["morning"]["candidate"]["recommendation_reason"] = ""
        decisions = evaluate_shadow_manifest(
            value,
            now=datetime(2026, 10, 6, 19, 0, tzinfo=JST),
        )
        self.assertEqual(decisions[0].action, ShadowAction.BLOCKED)
        self.assertIn("shadow_quality_gate", decisions[0].reason)


if __name__ == "__main__":
    unittest.main()
