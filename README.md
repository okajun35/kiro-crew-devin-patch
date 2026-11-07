# Kiro Crew Devin ACP patch

Kiro Crew から Devin CLI を選択して利用するための実験的なパッチです。
Kiro Crew の公式プロジェクトとは独立して管理しています。

## 内容

- Dashboard の Agent Harness から Devin を選択
- ACP ハンドシェイク、アカウントのモデル一覧、プロンプト、セッション再開
- MCP サーバーの接続と、Crew の既存承認経路へ接続する専用ブリッジ
- OS サンドボックスを維持し、ツール実行は都度の許可・拒否へルーティング
- フレームリプレイと Dashboard 選択テスト

## 対応バージョン

- Kiro Crew の基準コミット: `6de3b0aae57a8406ef30b39a40b3a5eef99321f5`
- Devin CLI: `3000.12.17`（実行時に検査し、異なるバージョンは停止）
- Devin の認証: `devin auth login` / `devin auth status`
- Python 3.12 以上、Kiro Crew 本体の開発環境

ネイティブのプロジェクト `.devin` 設定・フック等がある作業場所は、
Crew の承認経路を維持するため拒否します。共有セッション、ワーカー、
手動 compact などは未対応です。詳細は適用後の
`docs/system-specs/modules/harness-onboarding.md` の Devin 節を参照してください。

## 適用

```sh
python3 manage.py check /path/to/KiroCrew
python3 manage.py apply /path/to/KiroCrew
```

スクリプトはパッチの SHA-256 と適用可否を確認し、`apply` の場合だけ変更します。
基準コミットと異なる場合は警告します。競合時は停止し、自動修正しません。
適用後は Kiro Crew 本体の手順でビルドし、Gateway を再起動して、
Settings → Agent Harness から Devin を選びます。

```sh
python3 manage.py check-reverse /path/to/KiroCrew
python3 manage.py reverse /path/to/KiroCrew
```

`reverse` はパッチを逆適用します。適用後に同じ箇所を編集した場合は停止することがあります。
現在利用中の Gateway や個人の設定・認証ファイルは、このスクリプトから変更しません。

## 上流更新時

1. 本体の作業内容を別途保存する。
2. このパッチを逆適用できる状態にして外す。
3. 本体を更新する。
4. `check` で再適用を確認する。競合した場合はパッチを手で更新する。
5. 本体で関連テスト・ビルド・権限の許可と拒否を確認する。

パッチを更新したら `manifest.json` の基準コミット、CLI バージョン、
`patch_sha256`、検証記録も更新してください。本体全体のコピーは保持しません。

## 検証記録

- 関連バックエンドテスト 651 件通過
- Dashboard 関連テスト 48 件通過
- 変更した Python モジュールの型チェック、ドキュメント検査通過
- 実機で ACP、モデル一覧、プロンプト、プロセス再起動後の再開、MCP を確認
- Crew の実経路でツール拒否時の未実行、許可時の実行を確認

## ライセンス

このリポジトリ独自の適用スクリプト・ドキュメント・独自追加部分は MIT です。
パッチに含まれる Kiro Crew 由来のコード・文書・文脈行は元の Apache-2.0 を維持します。
パッチはそれらへの変更を含みます。元のライセンスと NOTICE は `licenses/` に同梱しています。
MIT の指定で上流由来部分の権利表示や条件を置き換えるものではありません。
