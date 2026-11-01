"""敏感数据脱敏工具。

对手机号、姓名、银行卡号等字段在接口返回层做脱敏，明文不外泄。
脱敏仅在输出（Schema）层进行，数据库中保留原始密文/明文（除密码外已哈希）。
"""

import re


def mask_mobile(mobile: str | None) -> str | None:
    """手机号脱敏：保留前3后4，中间4位替换为****。

    例如 "13800138000" -> "138****8000"。
    空值或不符合手机号格式时原样返回。
    """
    if not mobile:
        return mobile
    mobile = str(mobile).strip()
    if len(mobile) == 11 and mobile.isdigit():
        return f"{mobile[:3]}****{mobile[-4:]}"
    return mobile


def mask_real_name(name: str | None) -> str | None:
    """姓名脱敏：单字保留并显示“·”，双字显示首字+*，三字以上显示首尾并中间用*。

    例如 "张三" -> "张*"，"张三丰" -> "张*丰"。
    """
    if not name:
        return name
    name = str(name).strip()
    if len(name) == 1:
        return name
    if len(name) == 2:
        return f"{name[0]}*"
    return f"{name[0]}{'*' * (len(name) - 2)}{name[-1]}"


def mask_bank_card(card: str | None) -> str | None:
    """银行卡号脱敏：仅显示后4位，其余替换为****。

    例如 "6222021234567890" -> "************7890"。
    """
    if not card:
        return card
    card = str(card).strip()
    digits = re.sub(r"\D", "", card)
    if len(digits) <= 4:
        return card
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def mask_id_card(id_card: str | None) -> str | None:
    """身份证号脱敏：保留前6后4，中间替换为**********。

    例如 "110101199001011234" -> "110101********1234"。
    """
    if not id_card:
        return id_card
    id_card = str(id_card).strip()
    if len(id_card) <= 10:
        return id_card
    return f"{id_card[:6]}{'*' * 8}{id_card[-4:]}"


def mask_email(email: str | None) -> str | None:
    """邮箱脱敏：保留前缀首字符和域名，中间替换为****。

    例如 "zhangsan@example.com" -> "z***@example.com"。
    """
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return email
    masked_local = local[0] + "***"
    return f"{masked_local}@{domain}"


__all__ = [
    "mask_mobile",
    "mask_real_name",
    "mask_bank_card",
    "mask_id_card",
    "mask_email",
]
