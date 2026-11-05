<script setup lang="ts">
/** 经营看板页（RPT-01/02 实现）。 */
import { ref, onMounted } from 'vue'
import { useUserStore } from '@/stores/user'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import axios from 'axios'

const userStore = useUserStore()

// 概览数据
const overview = ref({
  todaySalesAmount: 0,
  todaySalesCount: 0,
  monthSalesAmount: 0,
  monthSalesCount: 0,
  totalInventoryValue: 0,
  totalInventoryQty: 0,
  lowStockCount: 0,
  totalReceivable: 0,
  totalPayable: 0,
  totalCustomers: 0,
  totalSuppliers: 0,
})

// 加载概览数据
const loadOverview = async () => {
  try {
    const res = await axios.get('/api/v1/dashboard/overview')
    if (res.data.code === 200) {
      overview.value = res.data.data
    }
  } catch (error) {
    console.error('加载概览数据失败:', error)
  }
}

// 销售趋势图表
const salesTrendChart = ref<HTMLElement>()
let salesTrendInstance: echarts.ECharts | null = null

const initSalesTrendChart = async () => {
  if (!salesTrendChart.value) return
  
  try {
    const res = await axios.get('/api/v1/dashboard/sales-trend?days=30')
    if (res.data.code === 200) {
      const data = res.data.data
      const dates = data.map((item: any) => item.date)
      const amounts = data.map((item: any) => Number(item.amount))
      
      salesTrendInstance = echarts.init(salesTrendChart.value)
      const option: EChartsOption = {
        title: {
          text: '销售趋势',
          left: 'center',
        },
        tooltip: {
          trigger: 'axis',
        },
        xAxis: {
          type: 'category',
          data: dates,
        },
        yAxis: {
          type: 'value',
          name: '金额(元)',
        },
        series: [
          {
            name: '销售额',
            type: 'line',
            data: amounts,
            smooth: true,
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(64, 158, 255, 0.3)' },
                { offset: 1, color: 'rgba(64, 158, 255, 0.05)' },
              ]),
            },
          },
        ],
      }
      salesTrendInstance.setOption(option)
    }
  } catch (error) {
    console.error('加载销售趋势数据失败:', error)
  }
}

// 库存分类图表
const inventoryCategoryChart = ref<HTMLElement>()
let inventoryCategoryInstance: echarts.ECharts | null = null

const initInventoryCategoryChart = async () => {
  if (!inventoryCategoryChart.value) return
  
  try {
    const res = await axios.get('/api/v1/dashboard/inventory-category')
    if (res.data.code === 200) {
      const data = res.data.data
      const categories = data.map((item: any) => item.category_name)
      const values = data.map((item: any) => Number(item.value))
      
      inventoryCategoryInstance = echarts.init(inventoryCategoryChart.value)
      const option: EChartsOption = {
        title: {
          text: '库存分类统计',
          left: 'center',
        },
        tooltip: {
          trigger: 'item',
          formatter: '{a} <br/>{b}: {c}元 ({d}%)',
        },
        legend: {
          orient: 'vertical',
          left: 'left',
        },
        series: [
          {
            name: '库存金额',
            type: 'pie',
            radius: '50%',
            data: data.map((item: any) => ({
              value: Number(item.value),
              name: item.category_name,
            })),
            emphasis: {
              itemStyle: {
                shadowBlur: 10,
                shadowOffsetX: 0,
                shadowColor: 'rgba(0, 0, 0, 0.5)',
              },
            },
          },
        ],
      }
      inventoryCategoryInstance.setOption(option)
    }
  } catch (error) {
    console.error('加载库存分类数据失败:', error)
  }
}

onMounted(async () => {
  await loadOverview()
  await initSalesTrendChart()
  await initInventoryCategoryChart()
  
  // 窗口大小变化时重绘图表
  window.addEventListener('resize', () => {
    salesTrendInstance?.resize()
    inventoryCategoryInstance?.resize()
  })
})
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">经营看板</h1>
        <p class="page-subtitle">
          欢迎回来，{{ userStore.user?.realName || userStore.user?.username }}！今日经营概览如下。
        </p>
      </div>
      
      <!-- 概览卡片 -->
      <el-row :gutter="20" class="overview-cards">
        <el-col :span="6">
          <el-card class="stat-card">
            <div class="stat-title">今日销售额</div>
            <div class="stat-value">¥{{ overview.todaySalesAmount.toLocaleString() }}</div>
            <div class="stat-subtitle">{{ overview.todaySalesCount }} 单</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card class="stat-card">
            <div class="stat-title">本月销售额</div>
            <div class="stat-value">¥{{ overview.monthSalesAmount.toLocaleString() }}</div>
            <div class="stat-subtitle">{{ overview.monthSalesCount }} 单</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card class="stat-card">
            <div class="stat-title">库存总金额</div>
            <div class="stat-value">¥{{ overview.totalInventoryValue.toLocaleString() }}</div>
            <div class="stat-subtitle">{{ overview.totalInventoryQty }} 件</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card class="stat-card">
            <div class="stat-title">低库存商品</div>
            <div class="stat-value warning">{{ overview.lowStockCount }}</div>
            <div class="stat-subtitle">需补货</div>
          </el-card>
        </el-col>
      </el-row>
      
      <!-- 应收应付 -->
      <el-row :gutter="20" class="finance-cards">
        <el-col :span="8">
          <el-card class="finance-card">
            <div class="finance-title">应收总额</div>
            <div class="finance-value receivable">¥{{ overview.totalReceivable.toLocaleString() }}</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card class="finance-card">
            <div class="finance-title">应付总额</div>
            <div class="finance-value payable">¥{{ overview.totalPayable.toLocaleString() }}</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card class="finance-card">
            <div class="finance-title">客户/供应商</div>
            <div class="finance-value">
              {{ overview.totalCustomers }} / {{ overview.totalSuppliers }}
            </div>
          </el-card>
        </el-col>
      </el-row>
      
      <!-- 图表区域 -->
      <el-row :gutter="20" class="chart-row">
        <el-col :span="16">
          <el-card>
            <div ref="salesTrendChart" class="chart-container"></div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card>
            <div ref="inventoryCategoryChart" class="chart-container"></div>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<style scoped>
.page-header {
  margin-bottom: 20px;
}

.overview-cards {
  margin-bottom: 20px;
}

.stat-card {
  text-align: center;
}

.stat-title {
  font-size: 14px;
  color: #606266;
  margin-bottom: 10px;
}

.stat-value {
  font-size: 24px;
  font-weight: bold;
  color: #303133;
  margin-bottom: 5px;
}

.stat-value.warning {
  color: #e6a23c;
}

.stat-subtitle {
  font-size: 12px;
  color: #909399;
}

.finance-cards {
  margin-bottom: 20px;
}

.finance-card {
  text-align: center;
}

.finance-title {
  font-size: 14px;
  color: #606266;
  margin-bottom: 10px;
}

.finance-value {
  font-size: 20px;
  font-weight: bold;
  color: #303133;
}

.finance-value.receivable {
  color: #67c23a;
}

.finance-value.payable {
  color: #f56c6c;
}

.chart-row {
  margin-bottom: 20px;
}

.chart-container {
  width: 100%;
  height: 400px;
}
</style>
