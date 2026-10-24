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

2026-10-24 15:23 JSTにPhase 9限定モードで、`RakutenROOMEngagement`（05:30）と`RakutenROOMExternalAudit`（20:45）の2タスクだけを登録した。両方とも有効、`StartWhenAvailable=True`、`MultipleInstances=IgnoreNew`、実行上限45分。登録前後の`RakutenROOMOrchestrator`定義SHA-256は一致し、旧4タスクは無効のまま。

登録日の15:23に交流タスクを実運用経路で1回起動し、07:30 cutoffによって終了コード0で安全にスキップされた。SQLiteは`status=skipped`、`last_error_code=cutoff_expired`、交流action 0件で、heartbeatにもURL・本文・候補ID・秘密情報は含まれなかった。20:45外部監査と翌日05:30交流の定刻観測が完了するまではPhase 9完了とはしない。問題があれば新2タスクを無効化し、投稿Orchestratorと旧ledger投影はそのまま継続できる。

2026-10-24 20:45 JSTの外部監査タスクは定刻実行された。初回結果は終了コード2で、当日3枠の実DB状態がすべて`UNCERTAIN`、未解決incident 3件、posting audit不合格であることを正しく検出した。これは正常日の誤警報ではないため、Phase 9完了およびPhase 10開始を停止する。Phase 9監査自身の不具合として、同日中に終端した日次交流heartbeatを3時間経過だけでstale扱いする問題を検出し、同一JST日付の`completed/skipped/partial/needs_human`を終端証跡として扱うよう修正した。追加7件・全379件のテストに合格し、再監査では誤った`heartbeat_stale`が消え、両heartbeatは`ok`、実在するUNCERTAIN関連だけが残った。監査・heartbeat 3ファイルの機密パターン検査は0件。

2026-10-25 00:44 JST、実ROOM上でrevision 316の3投稿を再照合した。各枠とも完全本文、全タグ、商品画像のショップ識別子（nenetshop / beefine / calmisence）がmanifest・DB attemptと一致し、profile商品数261の実表示内に存在した。再投稿せず、3枠を`UNCERTAIN -> POSTED`、3 attemptを`POSTED`、3 incidentを`resolved`へ原子的に更新し、旧ledgerへposted 3行を同期。2026-10-24 auditはエラー0で合格した。

共通原因は、認証済み投稿セッションで得た`api/collect`成功応答または投稿成功表示を`RoomLiveGateway`が捨て、直後のフィード再読込だけを唯一の確定証拠としていたこと。フィード反映遅延により3枠とも偽陰性になった。投稿セッション内のAPI成功・成功表示を確定証拠として保持し、閉じた画面や単なる遷移など曖昧な場合だけ従来の完全本文ROOM照合へフォールバックするよう修正した。曖昧な結果からはPOSTEDへ進まない回帰テストを含む対象18件・全381件が合格。

## Phase 9完了判定

2026-10-25 05:30 JSTの交流タスクは定刻に開始したが、共通45分上限により06:15に50フォロー・43いいねで終了コード`0x41306`となった。SQLiteには候補単位の進捗が保持され、候補キー重複は0件、認証・CAPTCHA・Chromeロック競合・投稿処理との競合はなかった。

原因は、投稿Orchestrator・外部監査・交流へ同じ45分設定を流用していたこと。正常な交流処理は実測で45分を超えるため、交流だけを75分、投稿Orchestratorと外部監査を45分のままに分離した。75分は05:30開始時でも06:45に終了し、07:00投稿Orchestratorまで15分の安全余白を残す。PowerShell構文、対象23件、全383件、`py_compile`、diff checkが合格した。

`-Phase9Only`で2タスクだけを再登録し、投稿OrchestratorのXML SHA-256が登録前後で同一であることを確認した。交流を06:17に1回だけ再開し、SQLite正本から既完了actionを再計上せず06:37に50/50・失敗0・終了コード0で完了した。DBは`completed`、owner解放、action 64件・候補キー重複0、heartbeatと公開要約は当日50/50で一致し、専用Chromeプロセスも残存しなかった。完了時刻を開始時刻で記録していた軽微な時刻不整合も、終了時刻を使う一般修正と当日状態の再整合で解消した。

最終外部監査は2026-10-24の確定済み3枠について`ok=true`、posting audit合格、両heartbeat合格、エラー0。外部監査・両heartbeatへのURL、候補ID、本文、秘密情報の混入は0件。旧4タスクはDisabled、新Phase 9タスクは交流05:30・外部監査20:45だけで、投稿Orchestratorは変更されていない。新2タスクを無効化すれば投稿Orchestratorを維持したまま戻せ、旧タスクXML・互換ledger・DBバックアップも保持されている。

以上によりPhase 9の完了条件をすべて満たした。Phase 9は完了、Phase 10（Codex限定自動復旧）の通知・診断のみ段階へ進行可能と判定する。

