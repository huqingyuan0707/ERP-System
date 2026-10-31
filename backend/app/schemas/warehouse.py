"""仓库相关 Schema：定义新增、部分更新、出参和查询参数，字段以 camelCase 对外。"""

from datetime import datetime  # 日期时间类型，用来声明仓库的创建时间。

from pydantic import BaseModel, ConfigDict, Field  # 模型基类、模型配置和字段校验规则。

from app.schemas.common import PageQuery, to_camel  # 复用分页参数和驼峰别名转换函数。


class WarehouseCreate(BaseModel):  # 继承 BaseModel，让 Pydantic 按字段声明解析和校验新增请求。
    """新增仓库；编码生成、负责人存在性校验和默认仓切换由 Service 层负责。"""

    model_config = ConfigDict(  # model_config 是 Pydantic 识别的配置名，配置作用于整个模型。
        from_attributes=True,  # 除字典外，也允许 model_validate 从对象属性读取字段。
        alias_generator=to_camel,  # 自动生成 managerId 等别名；按别名序列化时使用这些名称。
        populate_by_name=True,  # 有别名时仍接受 manager_id 等 Python 字段名，方便后端调用。
    )  # 这些配置只控制数据解析与别名，不会建表或保存数据。

    # 编码允许省略或留空，由业务层统一生成，不在 Schema 中访问数据库。
    code: str | None = Field(default=None, max_length=32)  # 编码可空，未传为 None；最长 32 字符。
    name: str = Field(min_length=1, max_length=64)  # 未设置默认值，因此必填；长度为 1～64 字符。
    manager_id: int | None = None  # 可选负责人 ID，未传为 None；此处不会检查用户是否存在。
    is_default: bool = False  # 布尔值表示是否设为默认仓；未传时不设为默认仓。
    address: str | None = Field(default=None, max_length=255)  # 地址可空，长度限制与仓库表一致。
    # 状态只允许 0（停用）或 1（启用），避免无效状态进入业务层。
    status: int = Field(default=1, ge=0, le=1)  # 默认启用；ge 表示大于等于，le 表示小于等于。
    remark: str | None = Field(default=None, max_length=255)  # 备注可不传；最多 255 字符。


class WarehouseUpdate(BaseModel):  # 单独声明更新模型，避免继承新增模型中必填的 name。
    """部分更新仓库；通过 model_dump(exclude_unset=True) 区分未传字段与显式传空。"""

    model_config = ConfigDict(  # 更新请求沿用新增请求的数据解析和别名规则。
        from_attributes=True,  # 允许从对象属性构造更新模型，不局限于字典输入。
        alias_generator=to_camel,  # 前端可以使用 managerId、isDefault 等 camelCase 字段名。
        populate_by_name=True,  # 后端也可以用 manager_id、is_default 等 snake_case 字段名。
    )  # 只定义解析配置；哪些字段真正写入数据库由后续 Service 决定。

    # 各字段均可省略；exclude_unset=True 会保留显式传入的 None、False 和 0。
    # Schema 允许 None 不代表数据库允许空值；非空列的显式 None 需由 Service 按业务规则处理。
    code: str | None = Field(default=None, max_length=32)  # 编码可省略；传字符串时仍限制 32 字符。
    name: str | None = Field(default=None, min_length=1, max_length=64)  # 可省略；字符串不能为空。
    manager_id: int | None = None  # 未传可保持原负责人；显式传 None 可用于请求清空负责人。
    is_default: bool | None = None  # 可省略或传布尔值；False 也是明确传入的更新值。
    address: str | None = Field(default=None, max_length=255)  # 可请求清空地址，长度上限不变。
    status: int | None = Field(default=None, ge=0, le=1)  # 不默认启用，避免意外修改原状态。
    remark: str | None = Field(default=None, max_length=255)  # 未传保留备注，传 None 可请求清空。


class WarehouseOut(BaseModel):  # 定义对外返回的数据结构，不直接把整条数据库记录返回给前端。
    """仓库出参；支持读取 ORM 属性，并附带负责人名称供页面展示。"""

    model_config = ConfigDict(  # 配置输出模型的数据来源和字段别名。
        from_attributes=True,  # 让 model_validate(仓库对象) 可以读取其 id、code 等 ORM 属性。
        alias_generator=to_camel,  # 按别名序列化时，将 manager_name、created_at 转为驼峰名称。
        populate_by_name=True,  # 接受 ORM 属性或后端字典中的原始 snake_case 字段名。
    )  # 序列化时仍需由调用方启用别名，例如 model_dump(by_alias=True)。

    id: int  # 必须提供仓库主键，前端依靠它发起详情、编辑或删除请求。
    code: str  # 必须提供已确定的仓库编码，不能像新增请求那样省略。
    name: str  # 必须提供仓库名称，用于列表和详情展示。
    manager_id: int | None = None  # 负责人关联 ID；没有负责人时可以返回 None。
    # 负责人名称不是仓库表字段，需由业务层补充关联查询结果。
    manager_name: str | None = None  # 展示用名称；仅有 manager_id 不会自动查询或填充此字段。
    is_default: bool = False  # 返回默认仓标记；来源缺少该属性时使用 False，不会修改数据库。
    address: str | None = None  # 返回仓库地址；None 在 JSON 中序列化为 null。
    status: int = 1  # 返回仓库状态；来源缺少该属性时默认 1，此处未设置 0/1 范围校验。
    created_at: datetime | None = None  # JSON 模式输出时间字符串；不会自动填入当前时间。


class WarehouseQuery(PageQuery):  # 继承 page、page_size、keyword，以及父类的 camelCase 别名配置。
    """仓库列表查询；复用分页与关键字参数，额外支持状态筛选。"""

    # 未传状态时不过滤；0 是有效的停用筛选值，不能按真假值判断是否传入。
    status: int | None = Field(default=None, ge=0, le=1)  # None 不筛选，0 查停用，1 查启用。
