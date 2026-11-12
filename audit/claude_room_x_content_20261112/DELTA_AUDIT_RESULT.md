# Independent delta audit — ROOM-linked X content planner

## VERDICT: PASS

- P0: 0
- P1: 0
- P2: 0
- ChatGPT final reviewへ進行可
- 本番公開・本番統合は未承認

## 解決確認

1. 所有ROOM詳細は`room.rakuten.co.jp/tora_papa/<数字>`に限定し、ROOM詳細が示す商品URL、事前選定の商品URL、slot、product IDを完全一致させる。
2. `x-daily:<date>`の冪等キー、plan ID、改ざん検知、外部永続ストアが原子的に発行したclaimをrelease条件にする。
3. 宣伝planとreleaseの両方が有料パートナーシップ表示を必須制御として宣言し、公式X接続がない状態では自動公開しない。
4. plan時とrelease時の文字数、役立つ投稿の短期重複、禁止表現、異常入力、HTTP URL加重をfail-closedで検査する。
5. 未投稿、不一致、他ROOM、改ざん、過長、URL埋込み、禁止表現の異常系テストを確認した。

## 残る前提条件

- 公式X送信接続、Paid Partnership表示の実適用、永続claimの原子的な取得・消費は本モジュール外の本番統合条件。本隔離モジュールの欠陥ではない。
- 本監査はX公開、API・認証、課金、DB、Windowsタスク、Actions、本番スケジュールを許可しない。

## 監査条件

- 対象: `src/room_x_content_planner.py`、`tests/test_room_x_content_planner.py`の2ファイルだけ。
- Claude Code 2.1.212、`claude-opus-4-8`、effort=`high`、ツール無効、MCPなし、セッション保存なし。
- 最大effortは使用していない。
- Claude側でテストは再実行せず、Codexの専用13件・全回帰434件合格証跡を前提に読み取り監査した。
