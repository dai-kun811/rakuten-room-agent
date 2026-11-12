# Independent audit — scheduled official-X integration

## VERDICT: CONDITIONAL_PASS

- P0: 0
- P1: 1
- P2: 7
- Parent agent final reviewへ進行可
- OAuth、課金、Developer Console変更、タスク登録、公開は未承認

## P1

### Paid Partnershipの実サービス反映を初回宣伝前に確認する

`src/room_x_api.py`は宣伝投稿に`paid_partnership=true`を必ず付与し、本文の
`#PR`もplannerとpublisherの両方で必須にしている。公式X API仕様にもこの
フィールドは掲載されているが、今回まだX APIを呼んでいないため、実アカウントで
有料パートナーシップ表示まで反映されることは未確認。初回宣伝公開直後に実画面で
表示を確認し、欠落時は以後の宣伝を停止することを本番開始条件にする。

## P2と対応

1. DPAPIのエラー番号取得が不安定
   - `WinDLL(..., use_last_error=True)`と`FormatError`へ補修し、実Windowsで暗号化
     round-tripを追加した。
2. 投稿作成前の一時失敗でも当日claimが残り再試行不能
   - 二重投稿防止を優先して現状維持。安全な再claimは別改善とする。
3. 単独publisherがrelease文書の改ざんを再検査しない
   - `release_id`、永続claim ID、posted証拠ID、商品URL一致をpublisher側でも再検査するよう補修。
4. 4週間分ぴったりで予備カードがない
   - 各型1本ずつ追加し、4型×5本の20本へ補修。
5. client secretをCLI引数で渡せる
   - CLI引数を削除し、必要時は環境からだけ読むよう補修。
6. refresh token更新後の保存失敗で旧tokenを失う可能性
   - 低確率の外部状態問題として継続監視。公開前に保存を完了する順序は維持。
7. manifest URLとROOM側canonical URLの形式差で安全停止し得る
   - plan作成時に楽天商品URLをcanonical形式へ統一するよう補修。

## 監査で確認された主な安全性

- 日単位の排他claim、receipt、uncertainで重複と結果不明時再送を停止。
- DB slotとattemptがともに`posted`で、URLが一致しない限りreleaseしない。
- 所有ROOM詳細`/tora_papa/<数字>`とリンク先商品を完全一致確認する。
- 対象枠の前にplanが作れなければ当日は宣伝しない。投稿可能時間は5分後から15分間。
- Windowsタスクは`IgnoreNew`、`StartWhenAvailable=false`で遅延起動しない。
- 将棋等のボードゲームを積み木コピーへ分類しない。

## 監査条件

- Claude Code 2.1.212
- `claude-opus-4-8`
- effort=`high`
- ツール無効、MCPなし、セッション保存なし、外部検索なし
- 初回はsandboxのDNS制限で`ENOTFOUND`となり推論結果なし。外部接続だけ許可して同一監査を1回実施。
- 最大effortは未使用。
