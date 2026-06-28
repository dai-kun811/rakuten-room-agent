# rakuten-room 引き継ぎ（HANDOFF）

> 新しいセッション（Codex）は AGENTS.md → このファイルの順で読み、前回の続きから作業する。
> 「現在の状態」だけを書く。詳細な仕様・運用は README.md。作業の区切り・セッション終了前・コンテキストが長くなったら必ず最新化する。

最終更新: 2026-06-28

## 現在のフェーズ
- 自動運用中。GitHub Actions 日次実行（`daily.yml`・日本時間07:00）で楽天ROOM投稿候補をGoogleスプレッドシートへ追記。
- 通常運用は固定ルール生成のみ（OpenAI/LLM不使用・API課金0）。

## 直近の状況（移設直後）
- 2026-06-28: 楽天の商品説明欄に混在する関連商品語で商品タイプと特徴が汚染される問題を修正。分類・固定ルール生成の根拠を商品名・カテゴリ・キャッチコピーへ限定し、鼻水吸引器用ノズルの専用文面、ホワイトノイズ機の優先分類、オムツ用ゴミ箱の除外と回帰テストを追加。Google Sheets の読み取りタイムアウトには3回まで再試行する。
- 2026-06-23: 本拠地統合に伴い `C:\Projects\rakuten-room` → `C:\Users\daiku\ai-company\projects\rakuten-room` へ移設。
- Codex向け `AGENTS.md` を新設（会社ルール・運用ガイド・移設情報）。
- git remote（dai-kun811/rakuten-room-agent・公開）・GitHub Actions は変更なしで稼働継続。

## 残タスク・次の一手
- 移設後に初めてCodexで開いたら **`.venv` を再作成**（`python -m venv .venv` → `pip install -r requirements.txt`）。

## 重要な決定事項
- リポジトリは**公開のまま維持**（GitHub Pages の routine page 配信のため。無料プランでprivate化するとページ停止）。

## 社長判断待ち
- なし（自動運用中）。
