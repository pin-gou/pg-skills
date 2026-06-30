"""Manifest — 读取 execution-manifest.yaml。"""

from __future__ import annotations

import os
from typing import Any


SUPPORTED_MANIFEST_VERSIONS = {"2026-06-30"}


def read_manifest(change_root: str) -> dict[str, Any]:
    """读取 execution-manifest.yaml。

    Args:
        change_root: change 根目录

    Returns:
        manifest dict

    Raises:
        FileNotFoundError: manifest 不存在
        ValueError: schema_version 不支持
    """
    path = os.path.join(change_root, "execution-manifest.yaml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} 不存在")

    try:
        import yaml as _yaml
        with open(path, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
    except Exception as e:
        raise ValueError(f"manifest 解析失败: {e}") from e

    sv = data.get("schema_version", "")
    if sv not in SUPPORTED_MANIFEST_VERSIONS:
        raise ValueError(
            f"manifest schema_version={sv!r} 不被支持. "
            f"支持: {sorted(SUPPORTED_MANIFEST_VERSIONS)}"
        )
    return data


def get_pipeline_order_from_manifest(change_root: str) -> list[str]:
    """从 manifest 提取 pipeline order。

    Returns:
        ["dev.backend", "dev.frontend", "final-gate", ...]
    """
    manifest = read_manifest(change_root)
    order: list[str] = []
    for stage in manifest.get("stages", []):
        stage_name = stage.get("name", "")
        for track in stage.get("tracks", []):
            tid = track["id"] if isinstance(track, dict) else track
            qualified = f"{stage_name}.{tid}" if stage_name else tid
            order.append(qualified)
    if "final_gate" in manifest:
        order.append("final-gate")
    return order