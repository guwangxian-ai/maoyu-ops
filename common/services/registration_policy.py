"""用户自助注册准入，参考上游 1e48313，保留 AGPL-3.0 来源。"""
from __future__ import annotations

from collections.abc import Mapping

REGISTRATION_ENABLED_KEY = "registration_enabled"
REGISTRATION_DISABLED_MESSAGE = "系统未开放注册，请联系管理员"


def is_registration_enabled(value: object) -> bool:
    """缺失或无法识别时关闭；兼容历史字符串布尔值。"""
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def get_registration_rejection_message(settings: Mapping[str, object]) -> str | None:
    if is_registration_enabled(settings.get(REGISTRATION_ENABLED_KEY)):
        return None
    return REGISTRATION_DISABLED_MESSAGE
