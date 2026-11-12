# Independent Audit — ROOM-linked X content planning

## VERDICT: CONDITIONAL_PASS

- P0: 0
- P1: 3
- P2: 8
- ChatGPT final reviewへ進行可
- 本番公開・本番統合は未承認

## P1 — 本番統合前の必須条件

1. `room_detail_url`は所有ROOM形式を確認しているが、対象商品のURLと同じ証拠から得たことをコード上で確認していない。誤った所有ROOM商品へリンクできる余地がある。
2. 二重公開防止が呼び出し側の`already_released`だけに依存し、`plan_date`／`plan_id`単位の永続的な冪等制御がない。
3. `requires_paid_partnership_label`はフラグだけであり、実際のX公開時に表示を有効化する処理は未実装。公式公開接続側で必須化する必要がある。

## P2 — 品質改善

1. 宣伝原稿の文字数はrelease時だけでなくplan時にも検査する。
2. 役立つ投稿の短期重複をコードでも拒否する。
3. 体験捏造・模倣は機械判定できないため、人間審査依存を明示する。
4. 禁止断定表現のdenylistを拡張する。
5. `Mapping`以外の入力を`XContentPlanError`として拒否する。
6. `http://`もURL加重文字数の対象にする。
7. 埋込みURL、宣伝禁止表現、商品ID重複、ROOM詳細URL異常、release文字数、商品URL不一致、役立つ投稿のリンク／`#PR`、不正version／日付を追加テストする。
8. 調査資料の「勝ちパターン」という表題を、因果未証明の観察仮説であることが伝わる表現にする。

## 肯定確認

- ネットワーク、ファイル、subprocess、DB、タスク、公開処理を含まない。
- 単一商品を事前選定し、未投稿・不一致・他ROOM・重複releaseを拒否する。
- 役立つ投稿と宣伝投稿を分離し、日本語のX加重文字数を検査する。
- ChatGPT final reviewへ進めるが、P1を閉じるまで本番統合しない。

## 監査条件

- 対象は監査brief、調査資料、新規source、新規testの4ファイルだけ。
- Claude Code 2.1.212、`claude-opus-4-8`、effort=`high`、ツール無効、MCPなし、セッション保存なし。
- 最大effortで開始した旧実行は社長指示により中断し、結果不採用。上記high実行だけを正式監査とする。
- Claude側でテストは再実行しておらず、テスト件数は親の証跡を採用。
- 本監査は本番公開・API認証・課金・タスク登録を許可しない。

## 初回監査後の補修（Codex、差分再監査前）

- P1-1: `room_detail_product_url`と事前選定商品のURLを完全一致させ、証拠IDも必須化。
- P1-2: `plan_date`単位の`idempotency_key`、改ざん検知、外部永続ストアが原子的に発行したclaimを必須化。
- P1-3: 有料パートナーシップ表示を将来の公式X送信接続の必須制御として保持。本隔離モジュールは送信しないため、本番統合前条件のまま。
- P2: plan時文字数、短期重複、禁止表現、入力型、HTTP URL加重、異常系テスト、表題の観察仮説化を反映。
- Codex検証: 専用13件、全回帰434件が合格。正式な判定更新はOpus・effort highの補修差分再監査後に行う。
