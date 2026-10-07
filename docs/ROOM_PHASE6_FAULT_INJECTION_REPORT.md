# Phase 6 障害注入試験記録

更新日: 2026-10-07 JST

## 結論

外部送信をmockしたOrchestratorで必須障害を検証し、Phase 6のゲートを通過した。本番ROOM、Sheets、GitHub Actions、Windowsタスクには接続していない。

## 試験結果

| 障害 | 安全側の結果 |
|---|---|
| DB commit前・claim中断 | transaction rollback、READY維持、attempt 0 |
| CLAIMED後クラッシュ | CLAIMED保持、再実行で外部送信0 |
| 投稿ボタン直前クラッシュ | CLAIMED保持、重複attempt拒否 |
| 投稿ボタン直後クラッシュ | SUBMITTINGからUNCERTAIN、NEEDS_HUMAN |
| 確認前・POSTED直前クラッシュ | SUBMITTED_UNCONFIRMEDからUNCERTAIN、再投稿0 |
| POSTED保存直後クラッシュ | POSTED終端、再送0、再実行でlegacy ledger同期回復 |
| GitHub/API/Actions一時障害 | AUTO_RETRY分類、retry budget対象 |
| manifest v2破損 | 受理前に拒否、DB状態不変 |
| PC再起動 | SQLite再open後もPOSTED維持 |
| 複数trigger同時起動 | idempotency keyとCASで送信1回 |
| Chromeロック競合 | AUTO_RETRY分類、外部送信なし |
| 前日UNCERTAIN | expireせず人間確認対象として保持 |
| 22:30超過 | EXPIRED_UNPOSTED、gateway呼出し0 |
| DB破損 | 初期化失敗、接続を確実にclose |
| disk full相当 | transaction rollback、slot/attempt不変 |
| git divergence | USER_ACTION_REQUIRED分類 |
| Codex上限到達 | BUDGET_EXHAUSTED |

## 検出・修正した問題

1. 破損DBを開いたとき、PRAGMA失敗後に接続ハンドルが残った。`connect()`をfail-closeに変更した。
2. 再起動時のSUBMITTINGを保持したままにしていた。active attemptを特定してUNCERTAINへ移し、NEEDS_HUMANを作るよう修正した。
3. POSTED commit後・legacy ledger同期前に落ちると、再実行が即no-opになりledger同期が欠落した。POSTED再実行時も互換同期だけを冪等実行するよう修正した。
4. 22:30以降の実行拒否がOrchestrator内部で強制されていなかった。JST cutoffを状態遷移前に追加した。

## 独立安全レビュー

- `POSTED`は終端で、再実行は外部送信しない。
- `SUBMITTING`以降は再投稿せずUNCERTAINへ倒す。
- slot CASとpost attempt idempotency keyを同一transactionで更新する。
- manifest URL・content hashがDB固定値と一致しない場合はclaim前に拒否する。
- CodexはPostGatewayを持たず、Orchestrator経由以外でROOMへ送信できない。
- 秘密情報をDB event、互換ledger、試験レポートへ保存しない。

## rollback

Phase 6は本番未接続のため、commitをrevertすればよい。DB→legacy ledger同期は追記型・冪等で、POSTEDを旧workerへ引き継げる。UNCERTAINがある場合は旧workerを再有効化しない。

## 完了判定

全331テスト合格、`git diff --check`合格。危険ケースの外部送信命令は重複0。Phase 7へ進行可能。
