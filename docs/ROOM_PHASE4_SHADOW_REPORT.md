# Phase 4 Orchestrator shadow mode 検証記録

更新日: 2026-10-06 JST

## 結論

Phase 4のshadow実装は完了したが、品質ゲートと最低3営業日の観測条件は未達である。Phase 5へは進まない。

ROOM投稿、Google Sheets書き込み、workflow dispatch、Windowsタスク変更、スケジュール変更、pushは行っていない。GitHub Actionsからの取得はHTTP GETだけで、状態DBはGit管理対象外の`reports/phase4-shadow`へ隔離した。

## 実装範囲

- manifest v2を朝・昼・晩の枠ごとに評価する読み取り専用Orchestrator
- JSTの枠時刻、過去日失効、既投稿URL、旧台帳の`posted` / `reserved` / 不確実な`failed`を考慮した判断
- 品質不合格を投稿候補としてclaimしない防御的ゲート
- manifestを複数日横断で監査するURL重複、本文類似度、7日・30日の商品タイプ集計
- Phase 3で追加した推薦理由、購入前確認点、根拠情報が欠ける古いartifactの検出
- 「自分で飲む」「セルフミルク」「ママ代行」「ハンズフリー授乳」を含む授乳補助商品の人手確認ルート
- 旧workerが選ぶ候補URLとshadow判断の差分記録
- 完了済みartifactの一時的な未検出に対する0.5秒後1回だけの再取得

## 実データ検証

対象はPhase 4開始日である2026-10-06 JST以降。最新の成功run `37382865080`、HEAD `ed72c63`、生成時刻2026-10-06 07:30 JSTをGETで取得した。

- 観測: 必須3営業日のうち1日
- manifest: 1件、候補3件
- URL重複: 0
- 最大本文類似度: 0.204
- 商品タイプ: kids_camera / stroller_storage / nursing_support 各1件
- 新Orchestrator: 3枠すべて`blocked`
- 旧worker: 同じ3 URLを投稿候補として選択
- 外部状態変更: 0

## 検出した問題

1. 実artifactのHEADは`origin/main`の`ed72c63`で、ローカルのPhase 1〜3 commitを含まない。3候補すべてで`recommendation_reason`、`purchase_checkpoints`、`source_evidence`が空だった。Phase 3改善後の実データ品質を証明するartifactではない。
2. evening候補の商品名に「赤ちゃん 自分で飲む」が含まれる授乳補助商品を旧処理は自動候補にしていた。誤使用時の影響が大きいため、shadowでは人手確認必須として遮断した。
3. morning本文には「撮った写真」が近接して繰り返され、文の接続も不自然だった。静的な長さ・タグ・類似度だけでは読みやすさを保証できないため、目視監査を継続する。
4. Phase 4開始後の観測は1営業日だけで、設計上必要な3営業日に達していない。

## テスト結果

- Phase 4専用テスト: 10件合格
- 全体回帰: 302件合格
- shadowレポート: `ready_for_phase5=false`
- 遮断理由: `only_1_of_3_required_days_observed`、`quality_gate_failed`、`legacy_decision_difference_detected`

## Phase 4再開条件

- Phase 3までのローカルcommitが反映された生成物を、外部状態を変更しない方法で用意する。
- その生成物で3枠すべての推薦理由、購入前確認点、根拠情報が埋まり、品質監査に合格する。
- Phase 4開始後の実データを最低3営業日分監査し、重大な判断差異が0件である。
- 授乳補助商品の安全ルールを生成側で扱うか、人手確認対象として運用契約に固定するかをPhase 4内で決める。

上記を満たすまでPhase 5へ進まない。
