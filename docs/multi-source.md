# 多来源分析

在控制台选择 DeepSeek。可添加本地文档或资料目录，勾选「搜索公开网页」，并选择一个配置好的 MySQL 查询表。「根据证据补查」也可单独用于本地文档。命令行通过 `--live --web --query-source ticket_demo` 启用相同来源。

## 公开网页

Agent 可用 Bing RSS 或 Wikimedia 搜索入口获取候选链接，并读取公开 HTTP(S) 页面原文。也可以读取分析问题里的具体公开链接。证据记录页面标题、原文段落、链接、读取日期，以及页面明确标出的发布日期。页面读取范围为 HTML 和纯文本。网页搜索只使用公开检索词。

## 自定义 MySQL 数据源

项目的 `runtime/query_sources.json` 为只读查询的数据源列表。也可用 `ANALYSIS_AGENT_QUERY_CONFIG` 指定配置文件的绝对路径。每条配置对应一个本机 MySQL 的表或视图。

```json
[
  {
    "id": "ticket_demo",
    "label": "工单台账",
    "database": "business_db",
    "table": "service_tickets",
    "user": "ticket_reader",
    "port": 3306,
    "key": "ticket_id",
    "columns": ["ticket_id", "symptom", "status", "minutes"]
  }
]
```

给独立账号授予该表或视图的 `SELECT` 权限，密码可通过 `ANALYSIS_AGENT_QUERY_TICKET_DEMO_PASSWORD` 或 Windows 通用凭据 `analysis-agent/MySQL/query/ticket_demo` 提供。查询使用只读事务，并记录 SQL、绑定参数、返回行数和记录编号。当前每个任务可选一个表，支持明细、条件、分组和 `COUNT`、`SUM`、`AVG`、`MIN`、`MAX`。结果超过 50 行时会要求缩小条件或改用聚合。

## 规划、执行与上下文

每轮规划最多试查两次。固定计划后，执行步骤根据复核反馈选择新的检索动作，每次执行最多尝试三轮，再沿用补证或部分报告流程。检索动作、证据与结果随任务检查点保存。

每次 DeepSeek 调用前对输入做 12,000 token 的预算检查。默认模型使用固定版本的 DeepSeek V4 tokenizer，预留消息框架开销。模型或 tokenizer 不匹配时按 UTF-8 字节数保守估算。超额时优先保留正在复核的引用，以本地 BGE 排序剩余原文片段。报告中的排除理由需要附带能在原文中核对的引文。
