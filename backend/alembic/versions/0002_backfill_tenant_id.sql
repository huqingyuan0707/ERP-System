-- 多租户 tenant_id 数据回填脚本
-- 适用于已有数据库，在执行 Alembic 升级后手动运行
-- 默认租户 ID 为 1，可根据实际业务调整

-- 1. bd_warehouse：回填 tenant_id，添加联合唯一索引
UPDATE bd_warehouse SET tenant_id = 1 WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_warehouse_code_tenant ON bd_warehouse (code, tenant_id);

-- 2. bd_goods：回填 tenant_id，添加联合唯一索引
UPDATE bd_goods SET tenant_id = 1 WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_goods_code_tenant ON bd_goods (code, tenant_id);

-- 3. bd_goods_category：回填 tenant_id，添加联合唯一索引
UPDATE bd_goods_category SET tenant_id = 1 WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_goods_category_name_tenant ON bd_goods_category (name, parent_id, tenant_id);

-- 4. bd_unit：回填 tenant_id，添加联合唯一索引
UPDATE bd_unit SET tenant_id = 1 WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_unit_name_tenant ON bd_unit (name, tenant_id);

-- 5. bd_supplier：回填 tenant_id，添加联合唯一索引
UPDATE bd_supplier SET tenant_id = 1 WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_code_tenant ON bd_supplier (code, tenant_id);

-- 6. bd_customer：回填 tenant_id，添加联合唯一索引
UPDATE bd_customer SET tenant_id = 1 WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_code_tenant ON bd_customer (code, tenant_id);

-- 7. inv_stock：回填 tenant_id，添加联合唯一索引
UPDATE inv_stock SET tenant_id = 1 WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_inv_stock_warehouse_goods_tenant ON inv_stock (warehouse_id, goods_id, tenant_id);

-- 8. inv_stock_flow：回填 tenant_id
UPDATE inv_stock_flow SET tenant_id = 1 WHERE tenant_id IS NULL;

-- 验证：查询各表未回填的记录（应为空）
-- SELECT 'bd_warehouse' AS tbl, count(*) AS missing FROM bd_warehouse WHERE tenant_id IS NULL
-- UNION ALL SELECT 'bd_goods', count(*) FROM bd_goods WHERE tenant_id IS NULL
-- UNION ALL SELECT 'inv_stock', count(*) FROM inv_stock WHERE tenant_id IS NULL;
