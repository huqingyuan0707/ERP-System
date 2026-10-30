# 企业ERP系统 · Vue3 前端页面清单（V1.0）

## 目录结构
```text
frontend/src/
├─ api/          # 按模块封装请求
├─ components/   # 分页表格、单据选择器、金额输入、导出按钮
├─ layout/       # 侧边菜单、顶栏、标签页
├─ router/       # 路由 + 权限守卫
├─ stores/       # Pinia：user/permission/dict/app
├─ utils/        # request(Axios)、auth、excel
└─ views/        # 页面
```

## 页面清单（含路由）
| 模块 | 路由 | 页面 | 主要功能 |
|---|---|---|---|
| 登录 | /login | 登录页 | 账号密码、验证码 |
| 首页 | /dashboard | 经营看板 | 今日销售额、库存总额、应收应付、图表 |
| 基础资料 | /base/goods | 商品管理 | CRUD、导入导出、分类/单位 |
| | /base/warehouse | 仓库管理 | CRUD、默认仓 |
| | /base/supplier | 供应商管理 | CRUD、信用/账期 |
| | /base/customer | 客户管理 | CRUD、信用额度 |
| | /base/employee | 员工/部门 | 员工、部门、岗位 |
| | /base/dict | 数据字典 | 字典类型/项维护 |
| 采购 | /purchase/apply | 采购申请 | 申请单、审批 |
| | /purchase/order | 采购订单 | 下单、审核、跟踪入库 |
| | /purchase/inbound | 采购入库 | 部分入库、多单入库 |
| | /purchase/return | 采购退货 | 退货出库 |
| 销售 | /sales/quotation | 销售报价 | 报价单 |
| | /sales/order | 销售订单 | 下单、审核、跟踪出库 |
| | /sales/outbound | 销售出库 | 部分出库 |
| | /sales/return | 销售退货 | 退货入库 |
| 库存 | /inventory/stock | 实时库存 | 按仓库/商品查询、可用量 |
| | /inventory/flow | 出入库流水 | 业务流水追溯 |
| | /inventory/transfer | 库存调拨 | 仓库间调拨 |
| | /inventory/check | 库存盘点 | 盘点单、差异调整 |
| | /inventory/alert | 库存预警 | 上下限、呆滞提醒 |
| 财务 | /finance/receivable | 应收管理 | 应收、已收、未收、账龄 |
| | /finance/payable | 应付管理 | 应付、已付、未付、账龄 |
| | /finance/receipt | 收款单 | 收款、核销应收 |
| | /finance/payment | 付款单 | 付款、核销应付 |
| | /finance/expense | 费用管理 | 费用登记 |
| | /finance/statement | 对账单 | 客户/供应商对账 |
| 报表 | /report/purchase | 采购报表 | 明细/汇总/供应商排行 |
| | /report/sales | 销售报表 | 明细/汇总/客户商品排行 |
| | /report/inventory | 库存报表 | 余额/流水/预警/呆滞 |
| | /report/finance | 财务报表 | 应收应付汇总、收付款明细 |
| 系统 | /system/user | 用户管理 | 新增、禁用、重置密码 |
| | /system/role | 角色权限 | 菜单/按钮/数据权限 |
| | /system/log | 操作日志 | 登录日志、操作日志 |
| | /system/config | 系统参数 | 公司信息、编号规则 |

## 通用能力
- 列表页统一：查询表单 + 分页表格 + 导出 Excel + 按钮级权限（`v-perm` 指令）
- 单据页统一：主单 + 明细行编辑 + 状态流转按钮 + 打印/导出
- 字典类字段走 Pinia 字典缓存，避免重复请求
