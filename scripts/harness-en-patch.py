#!/usr/bin/env python3
"""harness-en-patch.py — translate hard-coded Japanese strings in the
claude-code-harness binary to English so guardrail messages surface in
a language Aaron can read.

The harness plugin's guardrail rules (sudo deny, rm -rf prompt,
important-file warning, XSS warning, etc.) bake Japanese strings
directly into the Go source, so the i18n.language=en config knob does
not apply. Building a custom binary would require installing Go; this
script instead patches the compiled binary in place by byte-level
search-and-replace.

Each English replacement is padded with trailing spaces to match the
exact UTF-8 byte length of the original Japanese string. This keeps
all internal offsets/string-table indexes valid — the binary's symbol
table is not relocated.

Usage:
    python3 scripts/harness-en-patch.py                  # patch every
                                                         # detected binary
    python3 scripts/harness-en-patch.py --check          # report only,
                                                         # do not write

Affected binaries (auto-detected):
    ~/.claude/plugins/cache/claude-code-harness-marketplace/
        claude-code-harness/*/bin/harness-*

Idempotent: re-running after a successful patch finds zero matches and
exits clean. If a future plugin update reverts the strings, re-run this
script.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── translation table ────────────────────────────────────────────────
# Each tuple: (japanese_source, english_replacement).
# IMPORTANT: english must be <= japanese byte count after UTF-8 encode.
# The script pads english with trailing spaces to match exactly.
TRANSLATIONS: list[tuple[str, str]] = [
    # ── direct deny / require-confirmation messages ─────────────────
    (
        "sudo の使用は禁止されています。必要な場合はユーザーに手動実行を依頼してください。",
        "sudo is not allowed in commands. Ask the user to run it manually if needed.",
    ),
    (
        "--no-verify / --no-gpg-sign の使用は禁止されています。フックや署名検証を迂回しないでください。",
        "--no-verify / --no-gpg-sign is not allowed. Do not bypass hooks or signature verification.",
    ),
    (
        "git push --force は禁止されています。履歴を破壊する操作は許可されません。",
        "git push --force is not allowed. History-destructive operations are blocked.",
    ),
    (
        "protected branch への git reset --hard は禁止されています。履歴を壊さない方法を使ってください。",
        "git reset --hard on a protected branch is not allowed. Use a non-destructive method.",
    ),
    (
        "main/master への直接 push は設定で禁止されています。feature branch 経由で PR を作成してください。",
        "Direct push to main/master is disabled by config. Create a PR from a feature branch.",
    ),
    (
        "main/master への直接 push です。ユーザー確認後に実行しますか？(設定: protected_branch_push=ask)",
        "Direct push to main/master. Proceed after user confirmation? (config: protected_branch_push=ask)",
    ),
    (
        "main/master への直接 push です。ユーザー確認後に実行しますか？（設定: protected_branch_push=ask）",
        "Direct push to main/master. Proceed after user confirmation? (config: protected_branch_push=ask)",
    ),
    (
        "Codex モード中は Claude が直接ファイルを書き込めません。実装は Codex Worker (codex exec) に委譲してください。",
        "In Codex mode, Claude cannot write files directly. Delegate implementation to Codex Worker (codex exec).",
    ),
    (
        "Breezing reviewer ロールはファイル書き込みおよびデータ変更コマンドを実行できません。",
        "The Breezing reviewer role cannot write files or run data-changing commands.",
    ),

    # ── format strings (must preserve %s order) ─────────────────────
    (
        "%s は禁止されています: %s（%s）",
        "%s is not allowed: %s (%s)",
    ),
    (
        "%s は確認が必要です: %s（%s）",
        "%s needs confirmation: %s (%s)",
    ),
    (
        "警告: %s を検出しました: %s（%s）",
        "Warning: %s detected: %s (%s)",
    ),
    (
        "プロジェクトルート外への書き込みです: %s\n許可しますか？",
        "Write outside the project root: %s\nProceed?",
    ),
    (
        "危険な削除コマンドを検出しました:\n%s\n実行しますか？",
        "Dangerous deletion command detected:\n%s\nProceed?",
    ),
    (
        "警告: 重要ファイルへの変更を検出しました: %s",
        "Warning: change to an important file: %s",
    ),
    (
        "警告: 機密情報が含まれる可能性のあるファイルを読み取っています: %s",
        "Warning: reading a file that may contain secrets: %s",
    ),
    (
        "警告: 機密ファイルを読み取っています",
        "Warning: reading a sensitive file",
    ),
    (
        "警告: 機密ファイルの読み取りです",
        "Warning: sensitive file read",
    ),
    (
        "[v4] セキュリティリスク検出:\n%s",
        "[v4] Security risk detected:\n%s",
    ),
    (
        "保護パスへのファイル書き込み",
        "file write to protected path",
    ),
    (
        "保護パスへのシェル書き込み",
        "shell write to protected path",
    ),

    # ── pattern reasons surfaced via the %s slots above ─────────────
    (
        "ユーザー入力を innerHTML に設定しているコードを検出しました（XSS リスク）",
        "Code sets user input into innerHTML (XSS risk)",
    ),
    (
        "ユーザー入力を eval() に渡すコードを検出しました（RCE リスク）",
        "Code passes user input to eval() (RCE risk)",
    ),
    (
        "テンプレートリテラルを exec() に渡すコードを検出しました（コマンドインジェクションリスク）",
        "Code passes template literal to exec() (command injection risk)",
    ),
    (
        "ハードコードされた機密情報（パスワード/APIキー）を検出しました",
        "Hard-coded secret (password / API key) detected",
    ),
    (
        "機密情報を環境変数から直接文字列に埋め込んでいる可能性があります",
        "Secret may be embedded from env var into a string literal",
    ),
]

DEFAULT_GLOB = str(
    Path.home()
    / ".claude/plugins/cache/claude-code-harness-marketplace"
    "/claude-code-harness/*/bin/harness-*"
)


def patch_file(path: Path, check_only: bool) -> dict:
    """Return a stats dict: {found, replaced, errors, skipped}."""
    data = path.read_bytes()
    original = data
    stats = {"found": 0, "replaced": 0, "errors": 0, "skipped": 0}

    for ja, en in TRANSLATIONS:
        ja_bytes = ja.encode("utf-8")
        en_bytes = en.encode("utf-8")
        if len(en_bytes) > len(ja_bytes):
            print(
                f"  ERROR: {path.name}: '{en[:30]}...' "
                f"({len(en_bytes)} bytes) longer than source "
                f"({len(ja_bytes)} bytes). Skipped.",
                file=sys.stderr,
            )
            stats["errors"] += 1
            continue
        padded = en_bytes + b" " * (len(ja_bytes) - len(en_bytes))

        count = data.count(ja_bytes)
        if count == 0:
            stats["skipped"] += 1
            continue
        stats["found"] += count
        data = data.replace(ja_bytes, padded)
        stats["replaced"] += count

    if check_only:
        return stats

    if data == original:
        return stats

    backup = path.with_suffix(path.suffix + ".ja.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"  backed up -> {backup.name}")

    path.write_bytes(data)

    # macOS Gatekeeper kills any modified signed binary with SIGKILL on
    # exec. Ad-hoc re-sign so the patched darwin binaries still launch.
    # No-op for linux / windows / unsigned binaries.
    if sys.platform == "darwin" and "darwin" in path.name:
        try:
            subprocess.run(
                ["codesign", "--force", "--sign", "-", str(path)],
                check=True, capture_output=True,
            )
            print(f"  re-signed (ad-hoc) for Gatekeeper")
        except subprocess.CalledProcessError as e:
            print(
                f"  WARN: codesign failed on {path.name}: "
                f"{e.stderr.decode(errors='ignore').strip()}",
                file=sys.stderr,
            )

    return stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--check", action="store_true",
        help="Report what would change; do not write.",
    )
    p.add_argument(
        "--glob", default=DEFAULT_GLOB,
        help=f"Glob pattern for binaries to patch (default: {DEFAULT_GLOB})",
    )
    args = p.parse_args()

    binaries = sorted(Path(p) for p in glob.glob(args.glob))
    binaries = [b for b in binaries if b.is_file() and not b.name.endswith(".bak")]
    if not binaries:
        print(f"No binaries matched {args.glob}", file=sys.stderr)
        return 1

    print(f"Patching {len(binaries)} binary file(s):")
    total = {"found": 0, "replaced": 0, "errors": 0}
    for b in binaries:
        print(f" - {b}")
        s = patch_file(b, args.check)
        print(
            f"     found={s['found']} replaced={s['replaced']} "
            f"errors={s['errors']} skipped={s['skipped']}"
        )
        total["found"] += s["found"]
        total["replaced"] += s["replaced"]
        total["errors"] += s["errors"]

    print()
    verb = "would replace" if args.check else "replaced"
    print(
        f"Done. {total['found']} hits across {len(binaries)} files, "
        f"{verb} {total['replaced']}. Errors: {total['errors']}."
    )
    return 0 if total["errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
