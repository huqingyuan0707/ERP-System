"""数据库数量的共享校验；不通过舍入掩盖无效输入。"""

from decimal import Decimal

from app.core.exceptions import AppException, ErrorCode


DEFAULT_QUANTITY_PRECISION = 4
MAX_QUANTITY = Decimal("99999999999999.9999")


def validate_quantity(value: Decimal, precision: int = DEFAULT_QUANTITY_PRECISION) -> None:
    """校验非负 Numeric(18,4) 数量及单位精度，尾随零不计入精度。"""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    if not value.is_finite() or value < 0 or value > MAX_QUANTITY:
        raise AppException(ErrorCode.VALIDATION_ERROR, "数量必须是容量范围内的非负有限数")
    if not 0 <= precision <= DEFAULT_QUANTITY_PRECISION:
        raise AppException(ErrorCode.VALIDATION_ERROR, "数量精度必须在 0 至 4 位之间")
    if value == 0:
        return
    _, digits, exponent = value.as_tuple()
    trailing_zeros = 0
    for digit in reversed(digits):
        if digit != 0:
            break
        trailing_zeros += 1
    if exponent + trailing_zeros < -precision:
        raise AppException(ErrorCode.VALIDATION_ERROR, f"数量最多允许 {precision} 位小数")
