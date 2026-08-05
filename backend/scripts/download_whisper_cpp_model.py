#!/usr/bin/env python3
"""whisper.cpp用のGGMLモデルをHugging Faceから取得する。

Usage:
    python scripts/download_whisper_cpp_model.py base
    python scripts/download_whisper_cpp_model.py base --model-dir ./models/whisper_cpp
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HF_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

MODEL_ALIASES = {
    "tiny": "tiny",
    "base": "base",
    "small": "small",
    "medium": "medium",
    "large": "large-v1",
    "large-v1": "large-v1",
    "largev2": "large-v2",
    "large-v2": "large-v2",
}

AVAILABLE_MODELS = ("tiny", "base", "small", "medium", "large-v1", "large-v2")


def _resolve_model_dir(model_dir_arg: str | None) -> Path:
    if model_dir_arg:
        return Path(model_dir_arg)
    env_dir = (os.getenv("WHISPER_CPP_MODEL_DIR") or "").strip()
    if env_dir:
        return Path(env_dir)
    return Path("./models/whisper_cpp")


def _download_with_progress(url: str, dest: Path) -> None:
    def _reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        percent = min(100.0, downloaded * 100.0 / total_size)
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        print(f"\r  {percent:5.1f}% ({mb:.1f}/{total_mb:.1f} MB)", end="", flush=True)

    try:
        urllib.request.urlretrieve(url, dest, reporthook=_reporthook)
    except urllib.error.URLError as e:
        if dest.exists():
            dest.unlink()
        raise RuntimeError(f"ダウンロードに失敗しました: {e}") from e
    print()


def download_model(model_arg: str, model_dir: Path | None = None) -> Path:
    resolved_name = MODEL_ALIASES.get(model_arg)
    if not resolved_name:
        available = ", ".join(AVAILABLE_MODELS)
        raise ValueError(
            f"未対応のモデル名です: {model_arg}\n利用可能: {available}（別名: large→large-v1, largev2→large-v2）"
        )

    if model_dir is None:
        model_dir = _resolve_model_dir(None)
    model_dir = model_dir.expanduser()
    model_dir.mkdir(parents=True, exist_ok=True)

    dest = model_dir / f"ggml-{resolved_name}.bin"
    if dest.is_file():
        print(f"モデルは既に存在します（スキップ）: {dest}")
        return dest

    url = f"{HF_BASE_URL}/ggml-{resolved_name}.bin"
    print(f"ダウンロード中: {url}")
    print(f"保存先: {dest}")
    _download_with_progress(url, dest)
    print(f"完了: {dest}")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="whisper.cpp用のGGMLモデルをダウンロードする")
    parser.add_argument("model", help="モデル名 (tiny/base/small/medium/large-v1/large-v2)")
    parser.add_argument(
        "--model-dir",
        default=None,
        help="保存ディレクトリ（未指定時は環境変数 WHISPER_CPP_MODEL_DIR、それも無ければ ./models/whisper_cpp）",
    )
    args = parser.parse_args()
    try:
        model_dir = Path(args.model_dir) if args.model_dir else None
        download_model(args.model, model_dir=model_dir)
    except (ValueError, RuntimeError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
