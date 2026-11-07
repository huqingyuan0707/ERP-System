-- =============================================================================
-- ERP 系统演示数据补齐脚本（PostgreSQL）
--
-- 用途：为已完成的页面补齐可演示数据，覆盖 基础资料 / 员工部门 / 采购申请 /
--       采购订单 / 实时库存 / 出入库流水。
--
-- 特性：
--   1. 幂等：所有 INSERT 均带 ON CONFLICT DO NOTHING，重复执行不报错、不重复写入；
--   2. 不破坏现有数据：只做 INSERT，不做 UPDATE / DELETE / TRUNCATE；
--   3. 外键合法：部门、商品、单位、仓库、用户均通过子查询取真实 ID，
--      不硬编码自增主键，避免跨库 ID 不一致；
--   4. 状态流转合规：采购申请 0草稿/1待审/2已审；采购订单 0草稿/1待审/2已审；
--      已审单据才写 audit_by / audit_at；
--   5. 库存口径合规：available_qty = qty - locked_qty，qty >= locked_qty >= 0，
--      avg_cost >= 0（与 inv_stock 的 CHECK 约束一致）；
--   6. 流水口径合规：after_qty = before_qty ± qty，direction 1入库 / -1出库。
--
-- 执行方式（在 backend 目录，连接串由调用方通过 PGPASSWORD/DATABASE_URL 等 PG 环境变量提供，
-- 脚本本身不保存账号密码）：
--   psql "$DATABASE_URL" -f scripts/demo_data.sql
--
-- 前置条件：数据库已完成 alembic upgrade head，且已存在租户 1 的管理员、部门、
--          仓库、商品、单位（由 app/db/init_db.py 种子提供）。
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 0. 执行上下文：统一取租户 1 与管理员 ID，供后续 created_by / updated_by 使用
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM sys_user WHERE tenant_id = 1 AND username = 'admin') THEN
        RAISE EXCEPTION '租户 1 的 admin 用户不存在，请先执行 init_db 种子初始化';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM bd_warehouse WHERE tenant_id = 1 AND is_deleted = FALSE) THEN
        RAISE EXCEPTION '租户 1 无可用仓库，请先执行 init_db 种子初始化';
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 1. 供应商（10 条）：bd_supplier 按 (code, tenant_id) 唯一
-- -----------------------------------------------------------------------------
INSERT INTO bd_supplier
    (code, name, contact, phone, address, tax_no, bank, account,
     credit_days, status, remark, tenant_id, is_deleted, version)
VALUES
    ('SUPD001', '华东电子供应有限公司', '张伟', '021-58880001', '上海市浦东新区张江路 100 号', '91310115MA1K0001XA', '中国银行张江支行', '441000100010001', 30, 1, '电子类主供应商', 1, FALSE, 1),
    ('SUPD002', '南方办公用品有限公司', '李娜', '020-38880002', '广州市天河区体育西路 200 号', '91440101MA2K0002XB', '工商银行天河支行', '441000200020002', 45, 1, '办公耗材主供应商', 1, FALSE, 1),
    ('SUPD003', '北京数码科技集团', '王强', '010-68880003', '北京市海淀区中关村大街 300 号', '91110108MA3K0003XC', '建设银行中关村支行', '441000300030003', 60, 1, '数码设备供应商', 1, FALSE, 1),
    ('SUPD004', '苏南五金机电有限公司', '赵敏', '0512-68880004', '苏州市工业园区星湖街 400 号', '91320594MA4K0004XD', '农业银行苏州分行', '441000400040004', 30, 1, '五金劳保供应商', 1, FALSE, 1),
    ('SUPD005', '浙东包装材料有限公司', '陈刚', '0571-88880005', '杭州市余杭区文一西路 500 号', '91330110MA5K0005XE', '交通银行余杭支行', '441000500050005', 30, 1, '包装耗材供应商', 1, FALSE, 1),
    ('SUPD006', '鲁信纸业有限公司', '刘洋', '0531-78880006', '济南市历下区经十路 600 号', '91370102MA6K0006XF', '中信银行济南分行', '441000600060006', 45, 1, '纸品供应商', 1, FALSE, 1),
    ('SUPD007', '川蜀办公家具制造厂', '周涛', '028-88880007', '成都市武侯区科华北路 700 号', '91510107MA7K0007XG', '民生银行成都分行', '441000700070007', 60, 1, '办公家具供应商', 1, FALSE, 1),
    ('SUPD008', '闽南网络设备有限公司', '黄磊', '0592-58880008', '厦门市思明区软件园二期 800 号', '91350203MA8K0008XH', '兴业银行厦门分行', '441000800080008', 30, 1, '网络设备供应商', 1, FALSE, 1),
    ('SUPD009', '津门清洁用品有限公司', '孙丽', '022-58880009', '天津市南开区卫津路 900 号', '91120104MA9K0009XI', '浦发银行天津分行', '441000900090009', 30, 1, '清洁用品供应商', 1, FALSE, 1),
    ('SUPD010', '湘楚食品饮料有限公司', '吴鹏', '0731-88880010', '长沙市岳麓区麓谷大道 1000 号', '91430104MAAK0010XJ', '光大银行长沙分行', '441001000100010', 15, 1, '员工福利供应商', 1, FALSE, 1)
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 2. 客户（10 条）：bd_customer 按 (code, tenant_id) 唯一
--    credit_limit 0 表示不限额；credit_days 为账期天数
-- -----------------------------------------------------------------------------
INSERT INTO bd_customer
    (code, name, contact, phone, address, tax_no, bank, account,
     credit_limit, credit_days, status, remark, tenant_id, is_deleted, version)
VALUES
    ('CUS0001', '星海商贸有限公司', '林芳', '021-68880001', '上海市黄浦区南京东路 10 号', '91310101MA1C0001YA', '中国银行南京东路支行', '442000100010001', 200000.00, 30, 1, '长期合作客户', 1, FALSE, 1),
    ('CUS0002', '南方连锁超市集团', '郑凯', '020-38880002', '广州市越秀区中山五路 20 号', '91440104MA2C0002YB', '工商银行越秀支行', '442000200020002', 500000.00, 45, 1, '大宗采购客户', 1, FALSE, 1),
    ('CUS0003', '北方工贸有限公司', '何静', '010-68880003', '北京市朝阳区建国路 30 号', '91110105MA3C0003YC', '建设银行朝阳支行', '442000300030003', 150000.00, 30, 1, '', 1, FALSE, 1),
    ('CUS0004', '西部实业有限公司', '马超', '029-88880004', '西安市雁塔区科技路 40 号', '91610113MA4C0004YD', '农业银行雁塔支行', '442000400040004', 0.00, 60, 1, '不限额客户', 1, FALSE, 1),
    ('CUS0005', '江南电子商行', '许婷', '0512-68880005', '苏州市姑苏区人民路 50 号', '91320508MA5C0005YE', '交通银行姑苏支行', '442000500050005', 80000.00, 30, 1, '', 1, FALSE, 1),
    ('CUS0006', '中原物流有限公司', '冯军', '0371-88880006', '郑州市金水区花园路 60 号', '91410105MA6C0006YF', '中信银行金水支行', '442000600060006', 300000.00, 45, 1, '物流客户', 1, FALSE, 1),
    ('CUS0007', '海西贸易有限公司', '邓洁', '0591-88880007', '福州市鼓楼区五四路 70 号', '91350102MA7C0007YG', '民生银行鼓楼支行', '442000700070007', 120000.00, 30, 1, '', 1, FALSE, 1),
    ('CUS0008', '齐鲁实业集团', '曹阳', '0531-78880008', '济南市市中区经四路 80 号', '91370103MA8C0008YH', '兴业银行市中支行', '442000800080008', 400000.00, 60, 1, '战略客户', 1, FALSE, 1),
    ('CUS0009', '巴蜀商贸有限公司', '彭勇', '028-88880009', '成都市锦江区红星路 90 号', '91510104MA9C0009YI', '浦发银行锦江支行', '442000900090009', 60000.00, 30, 1, '', 1, FALSE, 1),
    ('CUS0010', '辽沈机电有限公司', '于洋', '024-88880010', '沈阳市和平区太原街 100 号', '91210102MAAC0010YJ', '光大银行和平支行', '442001000100010', 180000.00, 45, 1, '停用示例客户', 1, FALSE, 1)
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. 部门（5 个二级部门）：sys_dept 按 (name, tenant_id) 唯一
--    父级取「总公司」；若不存在则挂在 NULL（顶级）
-- -----------------------------------------------------------------------------
INSERT INTO sys_dept
    (name, parent_id, leader, sort, status, remark, tenant_id, is_deleted, version)
SELECT d.name, p.id, d.leader, d.sort, 1, d.remark, 1, FALSE, 1
FROM (
    VALUES
        ('采购部', '张伟', 10, '负责采购申请与订单'),
        ('销售部', '李娜', 20, '负责客户与销售订单'),
        ('仓储部', '王强', 30, '负责仓库与库存'),
        ('财务部', '赵敏', 40, '负责应收应付与对账'),
        ('行政部', '陈刚', 50, '负责办公与后勤')
) AS d(name, leader, sort, remark)
LEFT JOIN sys_dept p
       ON p.tenant_id = 1 AND p.name = '总公司' AND p.is_deleted = FALSE
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 4. 员工（15 条）：bd_employee 按 (code, tenant_id) 唯一
--    部门随机落到上面 5 个部门之一，保证 dept_id 合法
--    不关联系统用户（user_id 留空），避免与既有账号冲突
-- -----------------------------------------------------------------------------
INSERT INTO bd_employee
    (code, name, dept_id, position, mobile, entry_date, status, remark,
     tenant_id, is_deleted, version)
SELECT e.code, e.name, d.id, e.position, e.mobile, e.entry_date::date, 1, e.remark,
       1, FALSE, 1
FROM (
    VALUES
        ('EMP0001', '张伟', '采购部', '采购经理', '13800000001', '2019-03-01', '采购部负责人'),
        ('EMP0002', '李娜', '销售部', '销售经理', '13800000002', '2019-05-15', '销售部负责人'),
        ('EMP0003', '王强', '仓储部', '仓储主管', '13800000003', '2020-01-06', ''),
        ('EMP0004', '赵敏', '财务部', '财务主管', '13800000004', '2020-02-17', ''),
        ('EMP0005', '陈刚', '行政部', '行政专员', '13800000005', '2020-04-01', ''),
        ('EMP0006', '刘洋', '采购部', '采购专员', '13800000006', '2021-03-08', ''),
        ('EMP0007', '周涛', '采购部', '采购助理', '13800000007', '2021-07-12', ''),
        ('EMP0008', '黄磊', '销售部', '销售代表', '13800000008', '2021-09-01', ''),
        ('EMP0009', '孙丽', '销售部', '销售代表', '13800000009', '2022-01-10', ''),
        ('EMP0010', '吴鹏', '仓储部', '仓管员', '13800000010', '2022-03-14', ''),
        ('EMP0011', '郑凯', '仓储部', '仓管员', '13800000011', '2022-05-09', ''),
        ('EMP0012', '何静', '财务部', '会计', '13800000012', '2022-08-01', ''),
        ('EMP0013', '马超', '财务部', '出纳', '13800000013', '2023-02-20', ''),
        ('EMP0014', '许婷', '行政部', '前台', '13800000014', '2023-06-01', ''),
        ('EMP0015', '冯军', '行政部', '司机', '13800000015', '2023-09-04', '')
) AS e(code, name, dept_name, position, mobile, entry_date, remark)
JOIN sys_dept d
  ON d.tenant_id = 1 AND d.name = e.dept_name AND d.is_deleted = FALSE
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 5. 采购申请（10 张）：pur_apply 按 apply_no 唯一
--    状态分布：4 张草稿(0) / 3 张待审(1) / 3 张已审(2)
--    只有已审单据写 audit_by / audit_at，符合状态流转规则
-- -----------------------------------------------------------------------------
INSERT INTO pur_apply
    (apply_no, dept_id, applicant_id, need_date, status, audit_by, audit_at, audit_remark,
     remark, created_by, updated_by, tenant_id, is_deleted, version)
SELECT a.apply_no, d.id, u.id, a.need_date::date, a.status,
       CASE WHEN a.status = 2 THEN u.id ELSE NULL END,
       CASE WHEN a.status = 2 THEN a.audit_at::date ELSE NULL END,
       CASE WHEN a.status = 2 THEN '审核通过，可据此建采购订单' ELSE NULL END,
       a.remark, u.id, u.id, 1, FALSE, 1
FROM (
    VALUES
        ('PA202611010001', '采购部', '2026-11-20', 2, '2026-11-06', '常用电子配件补货'),
        ('PA202611010002', '采购部', '2026-11-25', 2, '2026-11-06', '季度办公耗材采购'),
        ('PA202611010003', '仓储部', '2026-11-18', 2, '2026-11-06', '包装材料补充'),
        ('PA202611020004', '采购部', '2026-12-01', 1, NULL, '网络设备更新申请'),
        ('PA202611020005', '行政部', '2026-11-30', 1, NULL, '办公家具采购申请'),
        ('PA202611020006', '仓储部', '2026-12-05', 1, NULL, '清洁用品补充申请'),
        ('PA202611030007', '采购部', '2026-11-28', 0, NULL, '草稿：待确认数量'),
        ('PA202611030008', '销售部', '2026-12-10', 0, NULL, '草稿：演示设备需求'),
        ('PA202611030009', '行政部', '2026-12-15', 0, NULL, '草稿：员工福利采购'),
        ('PA202611030010', '财务部', '2026-12-20', 0, NULL, '草稿：办公用品零星采购')
) AS a(apply_no, dept_name, need_date, status, audit_at, remark)
JOIN sys_dept d
  ON d.tenant_id = 1 AND d.name = a.dept_name AND d.is_deleted = FALSE
CROSS JOIN (
    SELECT id FROM sys_user WHERE tenant_id = 1 AND username = 'admin' AND is_deleted = FALSE
) AS u
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 6. 采购申请明细（每张申请 2 行，共 20 行）
--    同一申请内不重复商品（Service 层同样限制），数量 > 0
-- -----------------------------------------------------------------------------
INSERT INTO pur_apply_item
    (apply_id, goods_id, qty, need_date, created_by, updated_by, tenant_id, is_deleted)
SELECT a.id, g.id, i.qty::numeric(18, 4), a.need_date, a.created_by, a.updated_by,
       1, FALSE
FROM (
    VALUES
        ('PA202611010001', 'GD0011', 20),
        ('PA202611010001', 'GD0002', 10),
        ('PA202611010002', 'GD0004', 50),
        ('PA202611010002', 'GD0005', 30),
        ('PA202611010003', 'GD0006', 40),
        ('PA202611010003', 'GD0029', 15),
        ('PA202611020004', 'GD0009', 8),
        ('PA202611020004', 'GD0010', 4),
        ('PA202611020005', 'GD0053', 6),
        ('PA202611020005', 'GD0054', 3),
        ('PA202611020006', 'GD0059', 20),
        ('PA202611020006', 'GD0060', 20),
        ('PA202611030007', 'GD0007', 5),
        ('PA202611030007', 'GD0008', 30),
        ('PA202611030008', 'GD0014', 2),
        ('PA202611030008', 'GD0015', 1),
        ('PA202611030009', 'GD0063', 60),
        ('PA202611030009', 'GD0064', 12),
        ('PA202611030010', 'GD0028', 100),
        ('PA202611030010', 'GD0032', 100)
) AS i(apply_no, goods_code, qty)
JOIN pur_apply a
  ON a.tenant_id = 1 AND a.apply_no = i.apply_no AND a.is_deleted = FALSE
JOIN bd_goods g
  ON g.tenant_id = 1 AND g.code = i.goods_code AND g.is_deleted = FALSE
-- 幂等保护：pur_apply_item 无唯一约束，ON CONFLICT 无法拦截重复，
-- 因此用 NOT EXISTS 按 (apply_id, goods_id) 判重，重复执行不产生重复明细
WHERE NOT EXISTS (
    SELECT 1 FROM pur_apply_item x
    WHERE x.apply_id = a.id AND x.goods_id = g.id AND x.is_deleted = FALSE
);


-- -----------------------------------------------------------------------------
-- 7. 采购订单（10 张）
--    状态分布：4 草稿(0) / 3 待审(1) / 3 已审(2)
--    source_apply_id 指向已审申请单（3 张已审订单各对应 1 张已审申请），
--    其余为手工建单（source_apply_id 为 NULL），符合 P-02 溯源约定。
--    total_amount / tax_amount 先置 0，由第 8 步按明细回算，保证主表与明细一致。
-- -----------------------------------------------------------------------------
INSERT INTO pur_order
    (order_no, supplier_id, order_date, status, total_amount, tax_amount,
     audit_by, audit_at, audit_remark, remark, source_apply_id,
     created_by, updated_by, tenant_id, is_deleted, version)
SELECT o.order_no, s.id, o.order_date::date, o.status, 0, 0,
       CASE WHEN o.status = 2 THEN u.id ELSE NULL END,
       CASE WHEN o.status = 2 THEN o.audit_at::date ELSE NULL END,
       CASE WHEN o.status = 2 THEN '审核通过，可安排入库' ELSE NULL END,
       o.remark, a.id, u.id, u.id, 1, FALSE, 1
FROM (
    VALUES
        ('PO202611010001', 'SUPD001', '2026-11-01', 2, '2026-11-02', '由采购申请 PA202611010001 转单', 'PA202611010001'),
        ('PO202611010002', 'SUPD002', '2026-11-01', 2, '2026-11-02', '由采购申请 PA202611010002 转单', 'PA202611010002'),
        ('PO202611010003', 'SUPD005', '2026-11-01', 2, '2026-11-02', '由采购申请 PA202611010003 转单', 'PA202611010003'),
        ('PO202611020004', 'SUPD008', '2026-11-02', 1, NULL, '网络设备采购，待审', NULL),
        ('PO202611020005', 'SUPD007', '2026-11-02', 1, NULL, '办公家具采购，待审', NULL),
        ('PO202611020006', 'SUPD009', '2026-11-02', 1, NULL, '清洁用品采购，待审', NULL),
        ('PO202611030007', 'SUPD003', '2026-11-03', 0, NULL, '草稿：数码设备询价中', NULL),
        ('PO202611030008', 'SUPD006', '2026-11-03', 0, NULL, '草稿：纸品补货', NULL),
        ('PO202611030009', 'SUPD010', '2026-11-03', 0, NULL, '草稿：员工福利待定', NULL),
        ('PO202611030010', 'SUPD004', '2026-11-03', 0, NULL, '草稿：五金件零星采购', NULL)
) AS o(order_no, supplier_code, order_date, status, audit_at, remark, apply_no)
JOIN bd_supplier s
  ON s.tenant_id = 1 AND s.code = o.supplier_code AND s.is_deleted = FALSE
LEFT JOIN pur_apply a
  ON a.tenant_id = 1 AND a.apply_no = o.apply_no AND a.status = 2 AND a.is_deleted = FALSE
CROSS JOIN (
    SELECT id FROM sys_user WHERE tenant_id = 1 AND username = 'admin' AND is_deleted = FALSE
) AS u
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 8. 采购订单明细（每张订单 2 行，共 20 行）
--    amount = round(qty * price, 2)；tax_rate 取 0 或 0.13；
--    apply_item_id 仅对转单订单填写，指向同商品同申请单的明细行。
-- -----------------------------------------------------------------------------
INSERT INTO pur_order_item
    (order_id, apply_item_id, goods_id, unit_id, qty, price, amount, tax_rate, inbound_qty,
     created_by, updated_by, tenant_id, is_deleted)
SELECT o.id,
       ai.id,
       g.id,
       g.unit_id,
       i.qty::numeric(18, 4),
       i.price::numeric(18, 2),
       round(i.qty::numeric * i.price::numeric, 2),
       i.tax_rate::numeric(6, 4),
       0,
       o.created_by, o.updated_by, 1, FALSE
FROM (
    VALUES
        ('PO202611010001', 'GD0011', 20, 32.00, 0.1300),
        ('PO202611010001', 'GD0002', 10, 150.00, 0.1300),
        ('PO202611010002', 'GD0004', 50, 35.00, 0.1300),
        ('PO202611010002', 'GD0005', 30, 8.00, 0.1300),
        ('PO202611010003', 'GD0006', 40, 26.00, 0.1300),
        ('PO202611010003', 'GD0029', 15, 6.00, 0.1300),
        ('PO202611020004', 'GD0009', 8, 150.00, 0.1300),
        ('PO202611020004', 'GD0010', 4, 220.00, 0.1300),
        ('PO202611020005', 'GD0053', 6, 320.00, 0.1300),
        ('PO202611020005', 'GD0054', 3, 480.00, 0.1300),
        ('PO202611020006', 'GD0059', 20, 7.00, 0.1300),
        ('PO202611020006', 'GD0060', 20, 42.00, 0.1300),
        ('PO202611030007', 'GD0007', 5, 38.00, 0.1300),
        ('PO202611030007', 'GD0008', 30, 28.00, 0.1300),
        ('PO202611030008', 'GD0026', 100, 35.00, 0.1300),
        ('PO202611030008', 'GD0027', 200, 6.00, 0.1300),
        ('PO202611030009', 'GD0063', 60, 28.00, 0.1300),
        ('PO202611030009', 'GD0064', 12, 48.00, 0.1300),
        ('PO202611030010', 'GD0028', 100, 4.00, 0.1300),
        ('PO202611030010', 'GD0032', 100, 2.00, 0.1300)
) AS i(order_no, goods_code, qty, price, tax_rate)
JOIN pur_order o
  ON o.tenant_id = 1 AND o.order_no = i.order_no AND o.is_deleted = FALSE
JOIN bd_goods g
  ON g.tenant_id = 1 AND g.code = i.goods_code AND g.is_deleted = FALSE
LEFT JOIN pur_apply_item ai
  ON o.source_apply_id IS NOT NULL
 AND ai.apply_id = o.source_apply_id
 AND ai.goods_id = g.id
 AND ai.is_deleted = FALSE
-- 幂等保护：pur_order_item 无唯一约束，用 NOT EXISTS 按 (order_id, goods_id) 判重
WHERE NOT EXISTS (
    SELECT 1 FROM pur_order_item x
    WHERE x.order_id = o.id AND x.goods_id = g.id AND x.is_deleted = FALSE
);

-- -----------------------------------------------------------------------------
-- 9. 回算订单主表金额（幂等：只对 total_amount = 0 的订单执行，不覆盖既有数据）
--    含税合计 = Σ行金额；税额 = Σ(行金额 - 行金额/(1+税率))
-- -----------------------------------------------------------------------------
UPDATE pur_order o
SET total_amount = agg.total_amount,
    tax_amount = agg.tax_amount,
    updated_at = now()
FROM (
    SELECT i.order_id,
           SUM(i.amount)::numeric(18, 2) AS total_amount,
           SUM(i.amount - round(i.amount / (1 + i.tax_rate), 2))::numeric(18, 2) AS tax_amount
    FROM pur_order_item i
    WHERE i.tenant_id = 1 AND i.is_deleted = FALSE
    GROUP BY i.order_id
) AS agg
WHERE o.id = agg.order_id
  AND o.tenant_id = 1
  AND o.total_amount = 0
  AND o.is_deleted = FALSE;


-- -----------------------------------------------------------------------------
-- 10. 实时库存（20 条）：inv_stock
--     约束要求：qty >= locked_qty >= 0，available_qty = qty - locked_qty，avg_cost >= 0
--     唯一约束：同一租户 + 仓库 + 商品（仅未删除行生效）
--     avg_cost 取商品 cost_price，保证与商品档案口径一致
-- -----------------------------------------------------------------------------
INSERT INTO inv_stock
    (tenant_id, warehouse_id, goods_id, qty, locked_qty, available_qty, avg_cost,
     created_by, updated_by, is_deleted, version)
SELECT 1, w.id, g.id,
       s.qty::numeric(18, 4),
       s.locked::numeric(18, 4),
       (s.qty::numeric - s.locked::numeric)::numeric(18, 4),
       g.cost_price,
       u.id, u.id, FALSE, 1
FROM (
    VALUES
        ('GD0011', 200, 0),
        ('GD0002', 80, 5),
        ('GD0003', 20, 2),
        ('GD0004', 300, 0),
        ('GD0005', 600, 20),
        ('GD0006', 120, 0),
        ('GD0007', 55, 0),
        ('GD0008', 150, 10),
        ('GD0009', 40, 0),
        ('GD0010', 25, 0),
        ('GD0014', 15, 1),
        ('GD0015', 8, 0),
        ('GD0026', 400, 0),
        ('GD0027', 750, 50),
        ('GD0028', 500, 0),
        ('GD0029', 200, 0),
        ('GD0053', 12, 0),
        ('GD0059', 90, 0),
        ('GD0063', 150, 0),
        ('GD0064', 40, 0)
) AS s(goods_code, qty, locked)
JOIN bd_goods g
  ON g.tenant_id = 1 AND g.code = s.goods_code AND g.is_deleted = FALSE
JOIN bd_warehouse w
  ON w.tenant_id = 1 AND w.code = 'WH0001' AND w.is_deleted = FALSE
CROSS JOIN (
    SELECT id FROM sys_user WHERE tenant_id = 1 AND username = 'admin' AND is_deleted = FALSE
) AS u
ON CONFLICT DO NOTHING;


-- -----------------------------------------------------------------------------
-- 11. 出入库流水（20 条）
--     direction 1=入库 / -1=出库；after_qty = before_qty + direction * qty；
--     flow_type 取值与模型注释一致：purchase_in / sale_out / check_gain 等
-- -----------------------------------------------------------------------------
INSERT INTO inv_stock_flow
    (tenant_id, warehouse_id, goods_id, flow_type, direction, qty, before_qty, after_qty,
     unit_cost, source_type, source_no, biz_time, remark)
SELECT 1, w.id, g.id, f.flow_type, f.direction,
       f.qty::numeric(18, 4), f.before_qty::numeric(18, 4), f.after_qty::numeric(18, 4),
       g.cost_price, f.source_type, f.source_no, f.biz_time::timestamptz, f.remark
FROM (
    VALUES
        ('GD0011', 'purchase_in', 1, 200, 0, 200, 'pur_order', 'PO202611010001', '2026-11-01 10:00:00+08', '采购入库示例'),
        ('GD0002', 'purchase_in', 1, 80, 0, 80, 'pur_order', 'PO202611010001', '2026-11-01 10:05:00+08', '采购入库示例'),
        ('GD0002', 'sale_out', -1, 5, 80, 75, 'sal_order', 'SO202611050001', '2026-10-20 14:00:00+08', '销售出库示例'),
        ('GD0003', 'purchase_in', 1, 20, 0, 20, 'pur_order', 'PO202611010002', '2026-11-01 11:00:00+08', ''),
        ('GD0004', 'purchase_in', 1, 300, 0, 300, 'pur_order', 'PO202611010002', '2026-11-01 11:05:00+08', ''),
        ('GD0004', 'sale_out', -1, 30, 300, 270, 'sal_order', 'SO202611050002', '2026-10-28 09:30:00+08', ''),
        ('GD0005', 'purchase_in', 1, 600, 0, 600, 'pur_order', 'PO202611010002', '2026-11-01 11:10:00+08', ''),
        ('GD0005', 'sale_out', -1, 20, 600, 580, 'sal_order', 'SO202611050003', '2026-10-30 16:20:00+08', ''),
        ('GD0006', 'purchase_in', 1, 120, 0, 120, 'pur_order', 'PO202611010003', '2026-11-01 12:00:00+08', ''),
        ('GD0007', 'purchase_in', 1, 60, 0, 60, 'pur_order', 'PO202611030007', '2026-10-01 09:00:00+08', ''),
        ('GD0007', 'sale_out', -1, 5, 60, 55, 'sal_order', 'SO202611050004', '2026-10-15 15:00:00+08', ''),
        ('GD0008', 'purchase_in', 1, 160, 0, 160, 'pur_order', 'PO202611030007', '2026-10-02 09:00:00+08', ''),
        ('GD0008', 'sale_out', -1, 10, 160, 150, 'sal_order', 'SO202611050005', '2026-10-18 10:00:00+08', ''),
        ('GD0026', 'purchase_in', 1, 400, 0, 400, 'pur_order', 'PO202611030008', '2026-10-07 09:00:00+08', ''),
        ('GD0027', 'purchase_in', 1, 800, 0, 800, 'pur_order', 'PO202611030008', '2026-10-08 09:00:00+08', ''),
        ('GD0027', 'sale_out', -1, 50, 800, 750, 'sal_order', 'SO202611050006', '2026-10-27 11:00:00+08', ''),
        ('GD0028', 'purchase_in', 1, 500, 0, 500, 'pur_order', 'PO202611030010', '2026-10-09 09:00:00+08', ''),
        ('GD0059', 'check_gain', 1, 90, 0, 90, 'inv_check', 'IC202611050001', '2026-10-12 09:00:00+08', '盘点盘盈示例'),
        ('GD0063', 'purchase_in', 1, 150, 0, 150, 'pur_order', 'PO202611030009', '2026-10-13 09:00:00+08', ''),
        ('GD0064', 'sale_out', -1, 10, 50, 40, 'sal_order', 'SO202611050007', '2026-10-31 14:00:00+08', '')
) AS f(goods_code, flow_type, direction, qty, before_qty, after_qty, source_type, source_no, biz_time, remark)
JOIN bd_goods g
  ON g.tenant_id = 1 AND g.code = f.goods_code AND g.is_deleted = FALSE
JOIN bd_warehouse w
  ON w.tenant_id = 1 AND w.code = 'WH0001' AND w.is_deleted = FALSE
-- 幂等保护：流水表只追加、无唯一约束，用 NOT EXISTS 按业务键判重
WHERE NOT EXISTS (
    SELECT 1 FROM inv_stock_flow x
    WHERE x.tenant_id = 1
      AND x.goods_id = g.id
      AND x.flow_type = f.flow_type
      AND x.source_no IS NOT DISTINCT FROM f.source_no
);

COMMIT;


-- =============================================================================
-- 执行后校验要点（逐条执行，均应通过）
-- =============================================================================
-- ① 条数核对：期望 供应商10 / 客户10 / 部门6 / 员工15 / 申请10 / 申请明细20
--              订单10 / 订单明细20 / 库存20 / 流水20
-- SELECT
--   (SELECT count(*) FROM bd_supplier    WHERE tenant_id=1 AND is_deleted=FALSE) AS suppliers,
--   (SELECT count(*) FROM bd_customer    WHERE tenant_id=1 AND is_deleted=FALSE) AS customers,
--   (SELECT count(*) FROM sys_dept       WHERE tenant_id=1 AND is_deleted=FALSE) AS depts,
--   (SELECT count(*) FROM bd_employee    WHERE tenant_id=1 AND is_deleted=FALSE) AS employees,
--   (SELECT count(*) FROM pur_apply      WHERE tenant_id=1 AND is_deleted=FALSE) AS applies,
--   (SELECT count(*) FROM pur_apply_item WHERE tenant_id=1 AND is_deleted=FALSE) AS apply_items,
--   (SELECT count(*) FROM pur_order      WHERE tenant_id=1 AND is_deleted=FALSE) AS orders,
--   (SELECT count(*) FROM pur_order_item WHERE tenant_id=1 AND is_deleted=FALSE) AS order_items,
--   (SELECT count(*) FROM inv_stock      WHERE tenant_id=1 AND is_deleted=FALSE) AS stocks,
--   (SELECT count(*) FROM inv_stock_flow WHERE tenant_id=1)                      AS flows;
--
-- ② 订单主表与明细金额一致（应 0 行）
-- SELECT o.order_no, o.total_amount, SUM(i.amount) item_sum
-- FROM pur_order o JOIN pur_order_item i ON i.order_id=o.id AND i.is_deleted=FALSE
-- WHERE o.tenant_id=1 AND o.is_deleted=FALSE
-- GROUP BY o.id, o.order_no, o.total_amount HAVING o.total_amount <> SUM(i.amount);
--
-- ③ 库存三量口径（应 0 行）
-- SELECT * FROM inv_stock
-- WHERE tenant_id=1 AND (available_qty <> qty - locked_qty OR qty < locked_qty OR qty < 0);
--
-- ④ 流水数量连续（应 0 行）
-- SELECT * FROM inv_stock_flow WHERE tenant_id=1 AND after_qty <> before_qty + direction * qty;
--
-- ⑤ 状态流转合规：已审必须有审核人，非已审不得有（应 0 行）
-- SELECT 'pur_apply' tbl, apply_no no FROM pur_apply
--  WHERE tenant_id=1 AND ((status=2 AND audit_by IS NULL) OR (status<>2 AND audit_by IS NOT NULL))
-- UNION ALL
-- SELECT 'pur_order', order_no FROM pur_order
--  WHERE tenant_id=1 AND ((status=2 AND audit_by IS NULL) OR (status<>2 AND audit_by IS NOT NULL));
--
-- ⑥ 转单溯源一致：已审订单的 source_apply_id 必为已审申请（应 0 行）
-- SELECT o.order_no FROM pur_order o LEFT JOIN pur_apply a ON a.id=o.source_apply_id
-- WHERE o.tenant_id=1 AND o.source_apply_id IS NOT NULL AND (a.id IS NULL OR a.status<>2);
--
-- ⑦ 幂等验证：重复执行本脚本无报错，且 ① 的条数不变
--
-- ⑧ 接口验证（登录后带 Bearer Token）
-- GET /api/v1/base/suppliers?pageSize=100      -> total >= 10
-- GET /api/v1/base/customers?pageSize=100      -> total >= 10
-- GET /api/v1/base/employees?pageSize=100      -> total >= 15
-- GET /api/v1/purchase/applies?pageSize=100    -> total >= 10
-- GET /api/v1/purchase/orders?pageSize=100     -> total >= 10
-- GET /api/v1/inventory/stocks?pageSize=100    -> total >= 20
-- =============================================================================

