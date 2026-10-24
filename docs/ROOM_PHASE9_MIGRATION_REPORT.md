# ROOM Phase 9 migration report

実施日: 2026-10-24

## 実装範囲

- 交流処理の実行状態を `operations.db` の `engagement_runs` / `engagement_actions` で管理。
- 05:30より前は実行せず、07:30以降の遅延起動は `skipped/cutoff_expired` としてブラウザを開かない。
- 途中停止後はSQLiteの候補単位進捗を読み、同一routine_dateを再開できる。
- 投稿Orchestratorと共有するChromeプロファイルは非ブロッキングロックで保護し、投稿が使用中なら交流処理を安全にスキップする。
- ログイン切れ・CAPTCHAを `needs_human` と明示分類し、通常の候補処理エラーと分離。
- `room_external_audit.py` を追加。heartbeat欠落・stale、UNCERTAIN、未投稿、重大incident、SQLite異常、交流未完了を運用情報だけで監査する。
- 外部監査はlegacy ledgerを更新しない読み取り専用経路にした。
- heartbeatと外部監査レポートは役割、時刻、状態、件数、エラーコードのみを出力し、商品URL・候補ID・本文・認証情報を含めない。
- Orchestrator本体はheartbeat出力と監査の非破壊オプション追加以外の投稿ロジックを変更していない。
- Windowsタスク登録スクリプトに `RakutenROOMEngagement`（05:30）と `RakutenROOMExternalAudit`（20:45）の定義を追加。既存の旧タスクは無効のまま維持する。

## 検証

- `py_compile`: 合格
- Phase 9追加テスト: 6件合格
- 全回帰テスト: 378件合格
- PowerShell installer syntax parse: 合格
- 追加確認: cutoff拒否、途中停止後の再開、候補単位冪等性、非ブロッキングロック経路、heartbeat/監査出力の秘匿性を確認。

## 安全レビュー

- 交流処理は開始ゲートと非ブロッキングChromeロックを通過しない限りブラウザ操作を行わない。
- 07:30以降の遅延起動は自動追い上げせず、投稿処理を待たせない。
- 外部監査は公開向けの詳細データを生成せず、既存投稿状態を変更しない。
- 認証・CAPTCHAは自動継続せず `needs_human` として停止する。
- 既存Orchestrator、SQLite投稿状態、旧ledgerの互換投影をrollback対象として保持する。

## 有効化とrollback

今回の変更ではWindowsタスクの実登録・既存スケジュール変更は実行していない。登録時は既存のPhase 8 Orchestratorを保持したまま、交流と外部監査の2タスクだけを追加する。問題があれば新タスクを無効化し、投稿Orchestratorと旧ledger投影はそのまま継続できる。

