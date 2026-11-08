#!/usr/bin/env bash
# ローカルで開発サーバーを起動する(WSL・Linux・macOS)。
#   1. ルールの改正履歴のページを生成する(script/rule_history.py)
#   2. 本番(GitHub Actions)と同じ github-pages の版で jekyll serve する
# 使い方: script/serve.sh [jekyll serve に渡す追加の引数]
#
# - --baseurl "" : 認証情報がない環境では jekyll-github-metadata が公開パスを推定してしまうため空にする
# - --force_polling : WSLで /mnt/c 上のファイルを編集したとき、変更の通知が届かないため定期的に確かめる
# - gem は BUNDLE_PATH(既定: ~/.cache/portal-bundle)に入れる
#   (/tmp はWSLの再起動で消えるため使わない。リポジトリ内に入れると /mnt/c 上で遅くなる)
set -euo pipefail
cd "$(dirname "$0")/.."

export BUNDLE_PATH="${BUNDLE_PATH:-$HOME/.cache/portal-bundle}"
git submodule update --init _rules
python3 script/rule_history.py
bundle install --quiet
exec bundle exec jekyll serve --baseurl "" --force_polling "$@"
