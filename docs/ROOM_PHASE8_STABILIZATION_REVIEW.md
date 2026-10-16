# ROOM Phase 8 安定化レビュー（Day 1〜7）

## 結論

7日間の合格日は0/7だった。生成品質、重複防止、fence、DB/ledger整合性に共通する再発はなかった。一方、morning送信後の実ROOM照合が同日中に一時的な`UNCERTAIN`になる日が6日あり、Orchestratorが後続slotを一律停止していたため、noon/eveningが未投稿になる構造的な連鎖があった。

## 日別の障害

| 日付 | 直接原因 | 影響 | 判定 |
|---|---|---|---|
| 2026-10-09 | evening本文の品質不合格とgeneration budget消費 | evening未投稿 | 非合格 |
| 2026-10-10 | morning照合がUNCERTAIN、後続slotを停止 | noon/evening未投稿 | 非合格 |
| 2026-10-11 | pre-dispatch HEAD fence競合 | 生成未実行 | 非合格 |
| 2026-10-12 | morningが実ROOM上 confirmed_not_posted、後続slotを停止 | noon/evening未投稿 | 非合格 |
| 2026-10-13 | confirmed_not_posted URLの再選定、後続slotを停止 | noon/evening未投稿 | 非合格 |
| 2026-10-14 | morning照合がUNCERTAIN、後続slotを停止 | noon/evening未投稿 | 非合格 |
| 2026-10-15 | morning照合がUNCERTAIN、後続slotを停止 | noon/evening未投稿 | 非合格 |

## 横断分析

- Day 3のfence競合、Day 4〜5のconfirmed-absence再選定、Day 1の品質欠陥は既に一般修正済みで、後続日の再発証拠はない。
- Day 2・6・7は実ROOMに投稿が存在し、Day 4・5は実ROOMに存在しなかった。つまり`UNCERTAIN`は「投稿済み」の別名ではなく、照合タイミングで発生する正常な中間状態だった。
- 既存の`next_eligible_slot()`は、送信境界を越えた後の`UNCERTAIN`まで「送信中」と同じ扱いにしていた。そのため、morningの照合待ちがnoon/eveningへ伝播した。
- 送信途中の`CLAIMED`/`SUBMITTING`/`SUBMITTED_UNCONFIRMED`は、プロセスやブラウザが副作用を継続している可能性があるため、従来どおり後続slotを停止する必要がある。

## 根本修正

`UNCERTAIN` slotは自動再投稿せず、同一slotの再実行も行わないまま、独立した後続slotの選定だけを許可するように変更した。送信途中の3状態はfail-closedを維持した。これにより、morningの照合遅延・一時的なROOM反映遅延がnoon/eveningを巻き込まない。後続の実ROOM照合が`POSTED`または`confirmed_not_posted`を確定する責任は従来どおり維持する。

追加した回帰テストは、(1) morning UNCERTAIN後にnoonを選べる、(2) noon投稿後にeveningへ順序どおり進める、(3)送信途中状態は引き続き停止する、の3点を検証する。focused 8 tests、全371 testsが合格した。

## 安全レビューとrollback

- 新たな投稿API、認証、スケジュール、manifest、DB schemaは変更していない。
- UNCERTAINを再投稿可能状態へ戻す遷移は追加していない。既存のfence、slot所有権、content hash、post attempt冪等性がそのまま適用される。
- 変更は`src/room_phase8_runner.py`と回帰テストだけで、既存commitへ戻す通常のGit revertでrollbackできる。

## 今後の観測

安定化後の次のJST routine dateから、同じ合格条件で観測を再開する。まず「morningが一時UNCERTAINでもnoon/eveningが投稿される」ことを確認し、その後、3枠が全て実ROOM・DB・attempt・ledgerで整合する日を合格日として数える。Phase 9は7つの合格日が揃うまで開始しない。
