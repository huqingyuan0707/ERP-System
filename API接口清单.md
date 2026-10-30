# 企业ERP系统 · API 接口清单（V1.0）

- 前缀 `/api/v1`；认证 `Authorization: Bearer <token>`
- 响应 `{code,message,data}`；分页 `?page=1&page_size=20` → `data:{total,page,page_size,items}`

## 认证
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /auth/login | 登录返回 token |
| POST | /auth/refresh | 刷新 token |
| POST | /auth/logout | 退出 |
| GET | /auth/me | 当前用户信息+权限 |
| POST | /auth/password | 修改密码 |

## 系统管理
| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PUT/DELETE | /sys/users | 用户 CRUD |
| POST | /sys/users/{id}/reset-password | 重置密码 |
| GET/POST/PUT/DELETE | /sys/roles | 角色 CRUD |
| PUT | /sys/roles/{id}/permissions | 分配权限 |
| PUT | /sys/roles/{id}/data-scope | 数据范围 |
| GET | /sys/permissions/tree | 权限树 |
| GET | /sys/logs/oper | 操作日志 |
| GET | /sys/logs/login | 登录日志 |
| GET/POST/PUT/DELETE | /sys/dicts | 字典类型/项 |
| GET/PUT | /sys/configs | 系统参数 |

## 基础资料
| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PUT/DELETE | /base/goods | 商品 CRUD |
| GET | /base/goods/export | 导出 |
| POST | /base/goods/import | 导入 |
| GET/POST/PUT/DELETE | /base/categories | 商品分类 |
| GET/POST/PUT/DELETE | /base/units | 单位 |
| GET/POST/PUT/DELETE | /base/warehouses | 仓库 |
| GET/POST/PUT/DELETE | /base/suppliers | 供应商 |
| GET/POST/PUT/DELETE | /base/customers | 客户 |
| GET/POST/PUT/DELETE | /base/employees | 员工 |
| GET/POST/PUT/DELETE | /base/depts | 部门 |

## 采购
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /purchase/orders | 创建采购订单 |
| GET | /purchase/orders | 订单列表 |
| GET | /purchase/orders/{id} | 订单详情 |
| PUT | /purchase/orders/{id} | 修改 |
| POST | /purchase/orders/{id}/submit | 提交 |
| POST | /purchase/orders/{id}/audit | 审核 |
| POST | /purchase/orders/{id}/close | 关闭 |
| GET/POST | /purchase/inbounds | 入库单列表/创建 |
| POST | /purchase/inbounds/{id}/confirm | 确认入库（触发库存+应付） |
| GET/POST | /purchase/returns | 采购退货 |
| GET/POST | /purchase/applies | 采购申请 |

## 销售
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /sales/orders | 创建销售订单 |
| GET | /sales/orders | 列表 |
| POST | /sales/orders/{id}/audit | 审核 |
| GET/POST | /sales/outbounds | 出库单 |
| POST | /sales/outbounds/{id}/confirm | 确认出库（触发库存+应收） |
| GET/POST | /sales/returns | 销售退货 |
| GET/POST | /sales/quotations | 销售报价 |

## 库存
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /inventory/stocks | 实时库存 |
| GET | /inventory/flows | 出入库流水 |
| GET/POST | /inventory/transfers | 调拨单 |
| POST | /inventory/transfers/{id}/confirm | 确认调拨 |
| GET/POST | /inventory/checks | 盘点单 |
| POST | /inventory/checks/{id}/confirm | 确认盘点（差异调整） |
| GET | /inventory/alerts | 库存预警 |
| GET | /inventory/stocks/export | 库存导出 |

## 财务
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /finance/receivables | 应收列表/账龄 |
| GET | /finance/payables | 应付列表/账龄 |
| GET/POST | /finance/receipts | 收款单 |
| POST | /finance/receipts/{id}/writeoff | 核销应收 |
| GET/POST | /finance/payments | 付款单 |
| POST | /finance/payments/{id}/writeoff | 核销应付 |
| GET/POST | /finance/expenses | 费用登记 |
| GET/POST | /finance/statements | 对账单 |
| GET | /finance/statements/{id}/export | 导出对账单 |

## 报表
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /reports/dashboard | 经营看板数据 |
| GET | /reports/purchase | 采购报表（明细/汇总/供应商排行） |
| GET | /reports/sales | 销售报表（明细/汇总/客户商品排行） |
| GET | /reports/inventory | 库存报表（余额/流水/预警/呆滞） |
| GET | /reports/finance | 财务报表（应收应付汇总、收付款明细） |
| POST | /reports/export | 通用导出（异步任务） |
| GET | /tasks/{id} | 查询导出任务状态 |

## 通用约定
- 列表接口统一支持 `page`、`page_size`、`keyword`、`start_date`、`end_date` 及模块特有筛选
- 单据确认类接口（入库/出库/调拨/盘点）内部使用事务，保证库存与流水一致
- 权限：接口按 `perm_code` 校验（如 `pur:order:add`）
