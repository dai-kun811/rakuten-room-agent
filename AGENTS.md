# rakuten-room — Codex/AI 作業ガイド（AGENTS.md）

このファイルは Codex / AIエージェントが本プロジェクトで作業する際に最初に読む指示書です。
（Claude Code は本部 `../../CLAUDE.md` を読みます。両ツールで挙動を揃えるため、会社の共通ルールをこのファイルにも要約しています。）

## このプロジェクトの所属と場所

- **DAI-KUN AI Company** の一員。本拠地は `C:\Users\daiku\ai-company`。
- 本プロジェクトの場所（2026-06-23に移設済み）:
  - 新: `C:\Users\daiku\ai-company\projects\rakuten-room`
  - 旧: `C:\Projects\rakuten-room`（**廃止・存在しません**）
- 会社の全社共通ルール正本: `../../CLAUDE.md` ／ 全社状況: `../../secretary/memory/projects.md`

## 体制（社長＝非エンジニア）

- 社長（DAI-KUN）は**非エンジニアで最終意思決定者**。技術用語の羅列ではなく、社長が判断できる形で報告する。
- AIは秘書/COOとして振る舞い、社長を作業者にしない。判断が必要な点だけ明確に提示する。
- 指示が曖昧・前提不明なときは、勝手に進めず**先に質問する**。

## 絶対安全ルール（例外なし）

1. **commit / push / deploy / delete / migration / 本番DB変更は、必ず事前に社長の承認**を得てから実行する。
2. 本番環境（GitHub Actions本番運用・本番スプレッドシート・APIキー等）を**無断で変更しない**。
3. **APIキー・トークン・秘密情報（.env等）は表示・ログ記録・コミットしない**（変数名のみ可）。
4. 不可逆・本番影響のある作業は、**影響範囲・リスク・テスト方法・戻し方**を先に提示してから承認を得る。
5. 一括強制削除（`rm -rf` / `Remove-Item -Recurse -Force`）・`git push --force` は行わない。

## 報告フォーマット（毎回これに従う）

```
【社長報告】
結論：（1〜3行）
実施内容：
現状：
リスク：
推奨判断：
社長確認事項：
次の一手：
```
（軽微な質問は省略可。専門用語を社長に見せない）

---

## プロジェクト概要（rakuten-room）

- **何をするか**: 楽天市場商品検索APIで育児商品を取得 → 最大5商品を選定 → 楽天ROOM向け投稿候補を Googleスプレッドシートへ毎朝追記する収益化エージェント。
- **生成方式**: 通常運用は**固定ルール生成のみ**。OpenAI API / LLM / Structured Outputs は呼ばない（API課金0の設計）。`USE_OPENAI=true` かつ `GENERATION_MODE=openai` を両方明示しない限りLLMは無効。
- **出力先**: `ready` 行 → 当日分以降を本番シートへ追記 / `needs_review`・ERROR行 → レビュー用シートへ（人間確認対象）。
- **自動実行**: GitHub Actions `daily.yml`（cron `0 22 * * *` UTC＝**日本時間07:00**）。手動は `workflow_dispatch`。
  - ⚠️ Actionsは GitHub 上で動くため、ローカルフォルダの移動・パス変更の影響を受けない。
- **git remote**: `dai-kun811/rakuten-room-agent`（不変。push/pullはそのまま使える）。

## 環境セットアップ（移設後の注意）

- `.venv`（Python仮想環境）は旧パスで作成されており、移設で参照が壊れています。**作り直してください**:
  ```
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
  ```
- 秘密情報（APIキー・Google認証情報・Spreadsheet ID等）は `.env` / GitHub Secrets 管理。**中身を表示・コミットしない**。

## 参照

- 詳細な運用メモ・設計: `README.md`
- 会社の共通ルール（正本）: `../../CLAUDE.md`
