# MySQL 付款核对

控制台选择 DeepSeek 和已配置的付款台账，填写合同编号、计费周期，并上传合同或指定资料目录。合同原文须能核对编号、周期、年费和币种。确认范围后，Agent 提取合同年费，查询已入账的付款并计算差额。报告包含查询条件、付款记录编号及各项证据。

## 接入自己的台账

在本机 MySQL 的业务库中准备一张表或视图，内容为已入账付款。每行是一笔独立的付款，包含 `payment_id`、`contract_id`、`billing_period`、`amount` 和 `currency`。金额使用两位小数的 `DECIMAL`，币种使用 `CNY` 等三位代码。给 Agent 单独创建只读账号，仅授予该表或视图的 `SELECT` 权限。

在项目的 `runtime/business_sources.json` 中登记数据源。也可用 `ANALYSIS_AGENT_BUSINESS_CONFIG` 指定配置文件的绝对路径。

```json
[
  {
    "id": "my_payments",
    "label": "已入账付款台账",
    "database": "business_db",
    "table": "posted_payments",
    "user": "payments_reader",
    "port": 3306
  }
]
```

在启动控制台的进程环境中配置 `ANALYSIS_AGENT_BUSINESS_MY_PAYMENTS_PASSWORD`，或在 Windows 凭据管理器下使用通用凭据目标 `analysis-agent/MySQL/business/my_payments`。环境变量名中的数据源 ID 用大写。配置文件保存连接选项，密码由环境或凭据管理器提供。

查询按合同编号和计费周期精确筛选，每次核对最多 100 笔记录。查无记录、超出上限或币种不一致时，任务会显示缺证或冲突并保留已有结果。

## 本机演示台账

演示实例监听 `127.0.0.1:33316`。在 Windows 重启后，可以在 PowerShell 重新启动已初始化的演示台账。

```powershell
$mysql = 'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe'
$dataDir = Join-Path $env:LOCALAPPDATA 'analysis-agent\payments-demo-data'
Start-Process -FilePath $mysql -ArgumentList @('--no-defaults', ('--datadir="{0}"' -f $dataDir), '--mysqlx=OFF', '--bind-address=127.0.0.1', '--port=33316', '--skip-name-resolve') -WindowStyle Hidden
```
