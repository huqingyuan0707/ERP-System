<script setup lang="ts">
/** 经营看板页（RPT-01/02 实现）。 */
import { ref, onMounted, onUnmounted } from 'vue'
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
        backgroundColor: '#fff',
        title: {
          text: '销售趋势',
          left: 'left',
          textStyle: {
            color: '#1f2937',
            fontSize: 16,
            fontWeight: 600,
          },
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#fff',
          borderColor: '#e5e7eb',
          borderWidth: 1,
          textStyle: {
            color: '#374151',
          },
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          top: '15%',
          containLabel: true,
        },
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: false,
          axisLine: {
            lineStyle: {
              color: '#e5e7eb',
            },
          },
          axisLabel: {
            color: '#6b7280',
            fontSize: 12,
          },
        },
        yAxis: {
          type: 'value',
          name: '金额(元)',
          nameTextStyle: {
            color: '#6b7280',
            fontSize: 12,
          },
          axisLine: {
            show: false,
          },
          axisTick: {
            show: false,
          },
          splitLine: {
            lineStyle: {
              color: '#f3f4f6',
            },
          },
          axisLabel: {
            color: '#6b7280',
            fontSize: 12,
          },
        },
        series: [
          {
            name: '销售额',
            type: 'line',
            data: amounts,
            smooth: true,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: {
              width: 2,
              color: '#3b82f6',
            },
            itemStyle: {
              color: '#3b82f6',
              borderColor: '#fff',
              borderWidth: 2,
            },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(59, 130, 246, 0.3)' },
                { offset: 1, color: 'rgba(59, 130, 246, 0.05)' },
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
      
      inventoryCategoryInstance = echarts.init(inventoryCategoryChart.value)
      const option: EChartsOption = {
        backgroundColor: '#fff',
        title: {
          text: '库存分类统计',
          left: 'left',
          textStyle: {
            color: '#1f2937',
            fontSize: 16,
            fontWeight: 600,
          },
        },
        tooltip: {
          trigger: 'item',
          backgroundColor: '#fff',
          borderColor: '#e5e7eb',
          borderWidth: 1,
          textStyle: {
            color: '#374151',
          },
          formatter: '{a} <br/>{b}: ¥{c} ({d}%)',
        },
        legend: {
          orient: 'vertical',
          right: '5%',
          top: 'center',
          textStyle: {
            color: '#6b7280',
            fontSize: 12,
          },
        },
        series: [
          {
            name: '库存金额',
            type: 'pie',
            radius: ['45%', '70%'],
            center: ['35%', '50%'],
            avoidLabelOverlap: false,
            itemStyle: {
              borderRadius: 6,
              borderColor: '#fff',
              borderWidth: 2,
            },
            label: {
              show: false,
              position: 'center',
            },
            emphasis: {
              label: {
                show: true,
                fontSize: 16,
                fontWeight: 'bold',
                color: '#1f2937',
              },
              itemStyle: {
                shadowBlur: 10,
                shadowOffsetX: 0,
                shadowColor: 'rgba(0, 0, 0, 0.1)',
              },
            },
            labelLine: {
              show: false,
            },
            data: data.map((item: any, index: number) => ({
              value: Number(item.value),
              name: item.category_name,
              itemStyle: {
                color: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][index % 5],
              },
            })),
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
  
  window.addEventListener('resize', () => {
    salesTrendInstance?.resize()
    inventoryCategoryInstance?.resize()
  })
})

onUnmounted(() => {
  salesTrendInstance?.dispose()
  inventoryCategoryInstance?.dispose()
})
</script>

<template>
  <div class="dashboard-container">
    <!-- 头部 -->
    <div class="dashboard-header">
      <div>
        <h1 class="page-title">经营看板</h1>
        <p class="page-subtitle">
          欢迎回来，{{ userStore.user?.realName || userStore.user?.username }}
        </p>
      </div>
      <div class="date-badge">
        {{ new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }) }}
      </div>
    </div>

    <!-- 核心指标卡片 -->
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-header">
          <span class="metric-icon sales-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M3 3v14h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <path d="M7 10l3 3 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </span>
          <span class="metric-label">今日销售额</span>
        </div>
        <div class="metric-value">¥{{ overview.todaySalesAmount.toLocaleString() }}</div>
        <div class="metric-footer">
          <span class="metric-count">{{ overview.todaySalesCount }} 单</span>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-header">
          <span class="metric-icon month-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <rect x="2" y="3" width="16" height="14" rx="2" stroke="currentColor" stroke-width="2"/>
              <path d="M7 7h6M7 11h6M7 15h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </span>
          <span class="metric-label">本月销售额</span>
        </div>
        <div class="metric-value">¥{{ overview.monthSalesAmount.toLocaleString() }}</div>
        <div class="metric-footer">
          <span class="metric-count">{{ overview.monthSalesCount }} 单</span>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-header">
          <span class="metric-icon inventory-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M5 4l3-1 3 1 3-1 3 1v10l-3 1-3-1-3-1-3-1V4z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M5 4l3 1 3-1 3 1 3-1" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </span>
          <span class="metric-label">库存总金额</span>
        </div>
        <div class="metric-value">¥{{ overview.totalInventoryValue.toLocaleString() }}</div>
        <div class="metric-footer">
          <span class="metric-count">{{ overview.totalInventoryQty }} 件</span>
        </div>
      </div>

      <div class="metric-card warning">
        <div class="metric-header">
          <span class="metric-icon warning-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M10 2L2 18h16L10 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M10 8v4M10 14h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </span>
          <span class="metric-label">低库存商品</span>
        </div>
        <div class="metric-value warning-value">{{ overview.lowStockCount }}</div>
        <div class="metric-footer">
          <span class="metric-count">需补货</span>
        </div>
      </div>
    </div>

    <!-- 财务数据 -->
    <div class="finance-grid">
      <div class="finance-card">
        <div class="finance-icon receivable-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <path d="M21 12V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M3 12h18v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M12 12v9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </div>
        <div class="finance-content">
          <div class="finance-label">应收总额</div>
          <div class="finance-value receivable">¥{{ overview.totalReceivable.toLocaleString() }}</div>
        </div>
      </div>

      <div class="finance-card">
        <div class="finance-icon payable-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <path d="M21 12V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M3 12h18v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M12 12v9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </div>
        <div class="finance-content">
          <div class="finance-label">应付总额</div>
          <div class="finance-value payable">¥{{ overview.totalPayable.toLocaleString() }}</div>
        </div>
      </div>

      <div class="finance-card">
        <div class="finance-icon partner-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="9" cy="7" r="4" stroke="currentColor" stroke-width="2"/>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M16 3.13a4 4 0 0 1 0 7.75" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="finance-content">
          <div class="finance-label">客户 / 供应商</div>
          <div class="finance-value">{{ overview.totalCustomers }} / {{ overview.totalSuppliers }}</div>
        </div>
      </div>
    </div>

    <!-- 图表区域 -->
    <div class="charts-grid">
      <div class="chart-card">
        <div ref="salesTrendChart" class="chart-container"></div>
      </div>

      <div class="chart-card">
        <div ref="inventoryCategoryChart" class="chart-container"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard-container {
  padding: 24px;
  background: #f9fafb;
  min-height: 100vh;
}

/* 头部 */
.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  color: #111827;
  margin: 0 0 4px 0;
}

.page-subtitle {
  font-size: 14px;
  color: #6b7280;
  margin: 0;
}

.date-badge {
  padding: 8px 16px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  font-size: 14px;
  color: #6b7280;
  font-weight: 500;
}

/* 核心指标卡片 */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.metric-card {
  padding: 20px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  transition: all 0.2s ease;
}

.metric-card:hover {
  border-color: #d1d5db;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.metric-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.metric-icon {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}

.sales-icon {
  background: #3b82f6;
}

.month-icon {
  background: #8b5cf6;
}

.inventory-icon {
  background: #10b981;
}

.warning-icon {
  background: #f59e0b;
}

.metric-label {
  font-size: 13px;
  color: #6b7280;
  font-weight: 500;
}

.metric-value {
  font-size: 24px;
  font-weight: 600;
  color: #111827;
  margin-bottom: 8px;
}

.warning-value {
  color: #f59e0b;
}

.metric-footer {
  font-size: 12px;
  color: #9ca3af;
}

.metric-count {
  color: #6b7280;
}

/* 财务数据 */
.finance-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.finance-card {
  display: flex;
  align-items: center;
  padding: 20px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  transition: all 0.2s ease;
}

.finance-card:hover {
  border-color: #d1d5db;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.finance-icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 16px;
  color: #fff;
}

.receivable-icon {
  background: #10b981;
}

.payable-icon {
  background: #ef4444;
}

.partner-icon {
  background: #6366f1;
}

.finance-content {
  flex: 1;
}

.finance-label {
  font-size: 13px;
  color: #6b7280;
  font-weight: 500;
  margin-bottom: 4px;
}

.finance-value {
  font-size: 20px;
  font-weight: 600;
  color: #111827;
}

.finance-value.receivable {
  color: #10b981;
}

.finance-value.payable {
  color: #ef4444;
}

/* 图表区域 */
.charts-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
}

.chart-card {
  padding: 20px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.chart-container {
  width: 100%;
  height: 380px;
}
</style>
