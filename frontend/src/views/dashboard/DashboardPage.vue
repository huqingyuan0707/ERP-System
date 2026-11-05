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
        backgroundColor: 'transparent',
        title: {
          text: '销售趋势',
          left: 'center',
          textStyle: {
            color: '#303133',
            fontSize: 18,
            fontWeight: 'bold',
          },
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(50, 50, 50, 0.9)',
          borderColor: '#333',
          textStyle: {
            color: '#fff',
          },
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          containLabel: true,
        },
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: false,
          axisLine: {
            lineStyle: {
              color: '#e0e0e0',
            },
          },
          axisLabel: {
            color: '#606266',
          },
        },
        yAxis: {
          type: 'value',
          name: '金额(元)',
          axisLine: {
            show: false,
          },
          axisTick: {
            show: false,
          },
          splitLine: {
            lineStyle: {
              color: '#f0f0f0',
              type: 'dashed',
            },
          },
          axisLabel: {
            color: '#606266',
          },
        },
        series: [
          {
            name: '销售额',
            type: 'line',
            data: amounts,
            smooth: true,
            symbol: 'circle',
            symbolSize: 8,
            lineStyle: {
              width: 3,
              color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: '#409EFF' },
                { offset: 1, color: '#67C23A' },
              ]),
            },
            itemStyle: {
              color: '#409EFF',
              borderColor: '#fff',
              borderWidth: 2,
              shadowColor: 'rgba(64, 158, 255, 0.5)',
              shadowBlur: 10,
            },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(64, 158, 255, 0.3)' },
                { offset: 1, color: 'rgba(64, 158, 255, 0.05)' },
              ]),
            },
            animationDuration: 2000,
            animationEasing: 'cubicOut',
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
        backgroundColor: 'transparent',
        title: {
          text: '库存分类统计',
          left: 'center',
          textStyle: {
            color: '#303133',
            fontSize: 18,
            fontWeight: 'bold',
          },
        },
        tooltip: {
          trigger: 'item',
          backgroundColor: 'rgba(50, 50, 50, 0.9)',
          borderColor: '#333',
          textStyle: {
            color: '#fff',
          },
          formatter: '{a} <br/>{b}: ¥{c} ({d}%)',
        },
        legend: {
          orient: 'vertical',
          left: 'left',
          top: 'middle',
          textStyle: {
            color: '#606266',
          },
        },
        series: [
          {
            name: '库存金额',
            type: 'pie',
            radius: ['40%', '70%'],
            center: ['60%', '50%'],
            avoidLabelOverlap: false,
            itemStyle: {
              borderRadius: 10,
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
                fontSize: 20,
                fontWeight: 'bold',
                color: '#303133',
              },
              itemStyle: {
                shadowBlur: 20,
                shadowOffsetX: 0,
                shadowColor: 'rgba(0, 0, 0, 0.5)',
              },
            },
            labelLine: {
              show: false,
            },
            data: data.map((item: any, index: number) => ({
              value: Number(item.value),
              name: item.category_name,
              itemStyle: {
                color: [
                  '#409EFF',
                  '#67C23A',
                  '#E6A23C',
                  '#F56C6C',
                  '#909399',
                ][index % 5],
              },
            })),
            animationType: 'scale',
            animationEasing: 'elasticOut',
            animationDelay: (idx: number) => Math.random() * 200,
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

onUnmounted(() => {
  salesTrendInstance?.dispose()
  inventoryCategoryInstance?.dispose()
})
</script>

<template>
  <div class="dashboard-container">
    <!-- 头部欢迎区域 -->
    <div class="welcome-section">
      <div class="welcome-content">
        <h1 class="welcome-title">
          <span class="emoji">👋</span>
          欢迎回来，{{ userStore.user?.realName || userStore.user?.username }}！
        </h1>
        <p class="welcome-subtitle">今日经营概览如下</p>
      </div>
      <div class="current-time">{{ new Date().toLocaleDateString() }}</div>
    </div>

    <!-- 概览卡片 -->
    <div class="cards-grid">
      <div class="stat-card sales-card">
        <div class="card-icon">💰</div>
        <div class="card-content">
          <div class="card-label">今日销售额</div>
          <div class="card-value">¥{{ overview.todaySalesAmount.toLocaleString() }}</div>
          <div class="card-subtitle">{{ overview.todaySalesCount }} 单</div>
        </div>
        <div class="card-bg-circle"></div>
      </div>

      <div class="stat-card month-card">
        <div class="card-icon">📊</div>
        <div class="card-content">
          <div class="card-label">本月销售额</div>
          <div class="card-value">¥{{ overview.monthSalesAmount.toLocaleString() }}</div>
          <div class="card-subtitle">{{ overview.monthSalesCount }} 单</div>
        </div>
        <div class="card-bg-circle"></div>
      </div>

      <div class="stat-card inventory-card">
        <div class="card-icon">📦</div>
        <div class="card-content">
          <div class="card-label">库存总金额</div>
          <div class="card-value">¥{{ overview.totalInventoryValue.toLocaleString() }}</div>
          <div class="card-subtitle">{{ overview.totalInventoryQty }} 件</div>
        </div>
        <div class="card-bg-circle"></div>
      </div>

      <div class="stat-card warning-card">
        <div class="card-icon">⚠️</div>
        <div class="card-content">
          <div class="card-label">低库存商品</div>
          <div class="card-value warning">{{ overview.lowStockCount }}</div>
          <div class="card-subtitle">需补货</div>
        </div>
        <div class="card-bg-circle"></div>
      </div>
    </div>

    <!-- 财务数据 -->
    <div class="finance-section">
      <div class="finance-card receivable-card">
        <div class="finance-icon">📥</div>
        <div class="finance-content">
          <div class="finance-label">应收总额</div>
          <div class="finance-value receivable">¥{{ overview.totalReceivable.toLocaleString() }}</div>
        </div>
        <div class="finance-trend up">↑</div>
      </div>

      <div class="finance-card payable-card">
        <div class="finance-icon">📤</div>
        <div class="finance-content">
          <div class="finance-label">应付总额</div>
          <div class="finance-value payable">¥{{ overview.totalPayable.toLocaleString() }}</div>
        </div>
        <div class="finance-trend down">↓</div>
      </div>

      <div class="finance-card partner-card">
        <div class="finance-icon">🤝</div>
        <div class="finance-content">
          <div class="finance-label">客户 / 供应商</div>
          <div class="finance-value">{{ overview.totalCustomers }} / {{ overview.totalSuppliers }}</div>
        </div>
      </div>
    </div>

    <!-- 图表区域 -->
    <div class="charts-section">
      <div class="chart-card">
        <div class="chart-header">
          <h3 class="chart-title">销售趋势</h3>
          <div class="chart-badge">📈 实时数据</div>
        </div>
        <div ref="salesTrendChart" class="chart-container"></div>
      </div>

      <div class="chart-card">
        <div class="chart-header">
          <h3 class="chart-title">库存分类</h3>
          <div class="chart-badge">📊 统计分析</div>
        </div>
        <div ref="inventoryCategoryChart" class="chart-container"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard-container {
  padding: 24px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  min-height: 100vh;
}

/* 欢迎区域 */
.welcome-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 32px;
  padding: 24px 32px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
  backdrop-filter: blur(10px);
  animation: slideDown 0.6s ease-out;
}

@keyframes slideDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.welcome-content {
  flex: 1;
}

.welcome-title {
  font-size: 28px;
  font-weight: bold;
  color: #303133;
  margin: 0 0 8px 0;
  display: flex;
  align-items: center;
  gap: 12px;
}

.emoji {
  font-size: 32px;
  animation: bounce 2s infinite;
}

@keyframes bounce {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-10px);
  }
}

.welcome-subtitle {
  font-size: 16px;
  color: #909399;
  margin: 0;
}

.current-time {
  font-size: 14px;
  color: #606266;
  font-weight: 500;
  padding: 8px 16px;
  background: #f5f7fa;
  border-radius: 20px;
}

/* 概览卡片 */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 24px;
  margin-bottom: 32px;
}

.stat-card {
  position: relative;
  padding: 24px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 16px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  transition: all 0.3s ease;
  cursor: pointer;
  animation: fadeInUp 0.6s ease-out;
  animation-fill-mode: both;
}

.stat-card:nth-child(1) {
  animation-delay: 0.1s;
}

.stat-card:nth-child(2) {
  animation-delay: 0.2s;
}

.stat-card:nth-child(3) {
  animation-delay: 0.3s;
}

.stat-card:nth-child(4) {
  animation-delay: 0.4s;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stat-card:hover {
  transform: translateY(-8px);
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.15);
}

.card-icon {
  font-size: 48px;
  margin-bottom: 12px;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.1);
  }
}

.card-content {
  position: relative;
  z-index: 1;
}

.card-label {
  font-size: 14px;
  color: #909399;
  margin-bottom: 8px;
  font-weight: 500;
}

.card-value {
  font-size: 28px;
  font-weight: bold;
  color: #303133;
  margin-bottom: 4px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.card-value.warning {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.card-subtitle {
  font-size: 12px;
  color: #c0c4cc;
}

.card-bg-circle {
  position: absolute;
  width: 120px;
  height: 120px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%);
  top: -40px;
  right: -40px;
  animation: float 6s ease-in-out infinite;
}

@keyframes float {
  0%, 100% {
    transform: translateY(0) rotate(0deg);
  }
  50% {
    transform: translateY(-20px) rotate(180deg);
  }
}

/* 财务数据 */
.finance-section {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
  margin-bottom: 32px;
}

.finance-card {
  display: flex;
  align-items: center;
  padding: 24px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 16px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;
  animation: fadeInUp 0.6s ease-out 0.5s;
  animation-fill-mode: both;
}

.finance-card:hover {
  transform: scale(1.02);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
}

.finance-icon {
  font-size: 40px;
  margin-right: 16px;
}

.finance-content {
  flex: 1;
}

.finance-label {
  font-size: 14px;
  color: #909399;
  margin-bottom: 8px;
  font-weight: 500;
}

.finance-value {
  font-size: 24px;
  font-weight: bold;
  color: #303133;
}

.finance-value.receivable {
  color: #67c23a;
  text-shadow: 0 0 10px rgba(103, 194, 58, 0.3);
}

.finance-value.payable {
  color: #f56c6c;
  text-shadow: 0 0 10px rgba(245, 108, 108, 0.3);
}

.finance-trend {
  font-size: 24px;
  margin-left: 16px;
}

.finance-trend.up {
  color: #67c23a;
  animation: upDown 1s ease-in-out infinite;
}

.finance-trend.down {
  color: #f56c6c;
  animation: upDown 1s ease-in-out infinite reverse;
}

@keyframes upDown {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-5px);
  }
}

/* 图表区域 */
.charts-section {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 24px;
}

.chart-card {
  padding: 24px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 16px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  animation: fadeInUp 0.6s ease-out 0.6s;
  animation-fill-mode: both;
}

.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.chart-title {
  font-size: 18px;
  font-weight: bold;
  color: #303133;
  margin: 0;
}

.chart-badge {
  padding: 4px 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.chart-container {
  width: 100%;
  height: 400px;
}
</style>
