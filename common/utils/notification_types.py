"""通知事件的兼容解析，供配置保存与实际发送共用。"""
NOTIFY_TYPES = ("chat", "delivery", "account")


def normalize_notify_types(value) -> list[str]:
    values = value.split(",") if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        return list(NOTIFY_TYPES)
    picked = {str(item).strip() for item in values}
    return [item for item in NOTIFY_TYPES if item in picked] or list(NOTIFY_TYPES)


def allows_notification(subscription, config, event: str | None) -> bool:
    """省略事件时保留旧查询行为；两级订阅均允许时才发送对应事件。"""
    if event is None:
        return True
    channel_types = config.get("notify_types") if isinstance(config, dict) else None
    return event in normalize_notify_types(subscription) and event in normalize_notify_types(channel_types)
