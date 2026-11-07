# Kiro Crew Devin ACP patch

Kiro Crew から Devin CLI を利用するための、再利用用の実験的パッチです。
**Bot のハーネス選択、保存した設定の確認、Bot 操作・パネルの認証、ワーカーにも対応します。**
Kiro Crew の公式プロジェクトとは独立して管理し、公式への提出は予定していません。

## 配布するパッチ

| ファイル | 用途 |
| --- | --- |
| `patches/devin.patch` | 新規導入用。Devin ACP の基盤と Bot 対応・修正をすべて含む |
| `patches/devin-bot.patch` | このリポジトリで最初に配布した基盤パッチへの追加修正 |
| `patches/devin-base.patch` | 最初に配布した基盤の保存版。単独での新規導入には使わない |

本体全体のコピーは保持せず、変更分だけを管理します。
`manifest.json` に基準コミット、対応 CLI、各パッチの SHA-256 と検証結果を記録しています。

## 内容と制限

- Dashboard の Agent Harness と Bot の設定から Devin を選択
- ACP ハンドシェイク、アカウントのモデル一覧、プロンプト、セッション再開
- 保存した Bot 設定と、セッションが実際に読み込んだ設定の照合
- Bot 操作・パネル・ワーカーの MCP 接続
- Crew の承認経路に接続する専用ブリッジ。永続許可やバイパスを渡さない
- OS サンドボックスと認証情報のマスクを維持し、Bot の MCP はプロセスに結び付いた IPC 認証を利用
- 対象外の操作を拒否する検証スクリプト、フレームリプレイ、選択・保存・認証のテスト

対応する Devin CLI は **`3000.12.17`** です。異なるバージョンは実行時に停止します。
ネイティブのプロジェクト `.devin` 設定・フック等がある作業場所は拒否します。
共有ランタイム、読み取り専用 Side Chat、手動 compact、ネイティブの slash command との
完全な互換性は未対応です。Bot・ワーカーで適用できない個別ツール制限やポリシーも、
本体の既存検証に従って拒否します。詳細は適用後の
`docs/system-specs/modules/harness-onboarding.md` の Devin 節を参照してください。

## 基準と準備

- Kiro Crew の基準コミット: `6de3b0aae57a8406ef30b39a40b3a5eef99321f5`
- Python 3.12 以上と、Kiro Crew 本体の開発・ビルド環境
- Devin の認証: `devin auth login` / `devin auth status`
- 検証 OS: Linux。他の OS は未検証

## 新規導入

```sh
python3 manage.py check /path/to/KiroCrew
python3 manage.py apply /path/to/KiroCrew
```

## 最初の基盤パッチを適用済みの場合

```sh
python3 manage.py check /path/to/KiroCrew --bot-only
python3 manage.py apply /path/to/KiroCrew --bot-only
```

追加パッチは `devin-base.patch` の内容を前提とします。途中の Bot 実装や独自の編集がある
チェックアウトでは、その差分を先に整理してください。競合を自動的に上書きする処理はありません。

どちらの場合も、適用後は本体の手順でビルドし、Gateway を再起動してください。
Bot の設定で Devin を選び、新しい会話・ワーカーから確認します。
既存の会話は元のランタイムを使い続ける場合があります。

スクリプトはパッチの SHA-256 と適用可否を確認し、`apply` の場合だけコードを変更します。
基準コミットと異なる場合は警告し、競合時は停止します。
Gateway の再起動や個人の設定・認証ファイルの変更は行いません。

## 取り外し

一括パッチを取り外す場合:

```sh
python3 manage.py check-reverse /path/to/KiroCrew
python3 manage.py reverse /path/to/KiroCrew
```

Bot 追加分だけ外し、最初の基盤を残す場合:

```sh
python3 manage.py check-reverse /path/to/KiroCrew --bot-only
python3 manage.py reverse /path/to/KiroCrew --bot-only
```

適用後に同じ箇所を編集した場合は逆適用も停止することがあります。

## 上流更新時

1. 本体の独自変更を別途保存する。
2. このパッチを逆適用できる状態にして外す。
3. 本体を更新する。
4. `check` で再適用を確認する。競合した場合はパッチを手で更新する。
5. 関連テスト・ビルドと、権限の許可・拒否を確認する。

パッチを更新したら `manifest.json` の基準コミット、CLI バージョン、チェックサム、
検証記録も更新してください。最新の上流に無条件で適用できる保証はありません。

## 検証

- 関連バックエンドテスト **1,016 件**、Dashboard・翻訳関連 **149 件**通過
- フロントエンドのビルド、変更した Python の型チェック、ドキュメント検査通過
- 実 OS サンドボックス内の Bot MCP から、認証済み Gateway への status・panel 呼び出し成功
- 実 Devin + AcpClient で、保存済み設定確認、Bot MCP 認証、都度承認、シェル拒否、プロセス再起動後の再開を確認
- 隔離した Bot のワーカーで、専用 echo サーバーとマージされたプロンプトを確認

適用後に実 Devin の確認を行う場合:

```sh
.venv/bin/python scripts/probe_devin_member.py
```

この明示的な実機検証は、既存の Devin ログインを使って試験セッションを作成します。
一時的な Crew ホーム・Bot 設定・合成データを返す loopback Gateway を使い、
個人の Bot 設定や稼働中の Gateway は変更しません。承認するのは所定の読み取り用ツールと
ワーカーの echo のみです。生の記録を保存する `--record` は公開前に内容を確認してください。
通常の pytest は外部の Devin サービスに接続しません。

## ライセンス

このリポジトリ独自の適用スクリプト・ドキュメント・独自追加部分は MIT です。
パッチに含まれる Kiro Crew 由来のコード・文書・文脈行は元の Apache-2.0 を維持します。
パッチはそれらへの変更を含みます。元のライセンスと NOTICE は `licenses/` に同梱しています。
MIT の指定で上流由来部分の権利表示や条件を置き換えるものではありません。
