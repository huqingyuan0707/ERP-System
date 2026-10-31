$token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwidmVyIjo0LCJqdGkiOiJjOWZmN2FmNzY4MDU0YTIwOTYwZjczYjg1ZjUzY2I5YyIsInR5cGUiOiJhY2Nlc3MiLCJpYXQiOjE3OTM0MjI3NDUsImV4cCI6MTc5MzQyOTk0NX0.JOtCjD-5zhGMCRGqfyKiu-VM8V7WYWUPsOKxjaml3UQ"
$headers = @{Authorization = "Bearer $token"}

Write-Host "=== C-01: 工程基线、统一响应与 OpenAPI ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/health" -Method GET -Headers $headers -UseBasicParsing
    Write-Host "  /health: $($r.StatusCode) - $($r.Content)" -ForegroundColor Green
} catch {
    Write-Host "  /health: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/docs" -Method GET -UseBasicParsing
    Write-Host "  /docs: $($r.StatusCode) - OpenAPI 可访问" -ForegroundColor Green
} catch {
    Write-Host "  /docs: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== C-02: 登录、刷新、登出、改密 ===" -ForegroundColor Cyan
# 测试 /auth/me
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/auth/me" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /auth/me: $($r.StatusCode) - code=$($content.code), user=$($content.data.username), perms=$($content.data.permissions.Count), menus=$($content.data.menus.Count)" -ForegroundColor Green
} catch {
    Write-Host "  /auth/me: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== C-03: 菜单、按钮权限与动态路由 ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/sys/permissions/tree" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /sys/permissions/tree: $($r.StatusCode) - code=$($content.code), items=$($content.data.Count)" -ForegroundColor Green
} catch {
    Write-Host "  /sys/permissions/tree: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== C-05: 字典、参数、单号运行时 ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/sys/dicts" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /sys/dicts: $($r.StatusCode) - code=$($content.code)" -ForegroundColor Green
} catch {
    Write-Host "  /sys/dicts: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/sys/configs" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /sys/configs: $($r.StatusCode) - code=$($content.code)" -ForegroundColor Green
} catch {
    Write-Host "  /sys/configs: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== C-06: 操作/登录日志与审计 ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/sys/logs/login" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /sys/logs/login: $($r.StatusCode) - code=$($content.code), total=$($content.data.total)" -ForegroundColor Green
} catch {
    Write-Host "  /sys/logs/login: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/sys/logs/oper" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /sys/logs/oper: $($r.StatusCode) - code=$($content.code), total=$($content.data.total)" -ForegroundColor Green
} catch {
    Write-Host "  /sys/logs/oper: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== C-07: Excel 导入导出与异步任务 ===" -ForegroundColor Cyan
# 测试商品导出（需要权限 base:goods:export）
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/base/goods/export" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /base/goods/export: $($r.StatusCode) - code=$($content.code)" -ForegroundColor Green
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 403) {
        Write-Host "  /base/goods/export: 403 Forbidden (无导出权限，预期)" -ForegroundColor Yellow
    } else {
        Write-Host "  /base/goods/export: ERROR - $($_.Exception.Message)" -ForegroundColor Red
    }
}

# 测试库存导出
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/inventory/stocks/export" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /inventory/stocks/export: $($r.StatusCode) - code=$($content.code)" -ForegroundColor Green
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 403) {
        Write-Host "  /inventory/stocks/export: 403 Forbidden (无导出权限，预期)" -ForegroundColor Yellow
    } else {
        Write-Host "  /inventory/stocks/export: ERROR - $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== C-04: 数据权限（本人/本部门/全部） ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/sys/roles" -Method GET -Headers $headers -UseBasicParsing
    $content = $r.Content | ConvertFrom-Json
    Write-Host "  /sys/roles: $($r.StatusCode) - code=$($content.code), roles=$($content.data.Count)" -ForegroundColor Green
    foreach ($role in $content.data) {
        Write-Host "    Role: id=$($role.id), code=$($role.code), name=$($role.name), dataScope=$($role.dataScope)"
    }
} catch {
    Write-Host "  /sys/roles: ERROR - $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== 测试完成 ===" -ForegroundColor Cyan
