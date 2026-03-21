---
schedule: "0 8 * * *"
description: 每日销售情报搜索与日报生成（含融资专项）
---

# 每日销售情报

## 执行步骤

1. 运行 `scripts/main.py` 执行完整 pipeline
2. 系统自动判断是否为行业搜索日（周一、周四）
3. 融资专项搜索仅在周一、周四执行（周一搜集上周四至周一，周四搜集周一至周四）
4. 如果生成了日报，推送到飞书频道
5. 如果无新情报，跳过本次推送

## 命令

```bash
cd /path/to/sales-intel && python scripts/main.py --profile 张三
```

## 异常处理

- Tavily API 调用失败：记录错误，继续处理已获取的结果
- 全部搜索失败：跳过本次日报，不推送空报告
- 去重后无新结果：跳过本次日报
- 融资搜索无结果：跳过融资板块，其他板块正常生成

## 记忆系统（小龙虾 Agent 调用）

当小龙虾与用户交互时，如检测到以下类型的用户指令，需要记录偏好：

```python
from scripts.memory import record_interaction, record_feedback

# 用户查询某品牌/行业详情
record_interaction("宇树科技", "brand", "asked_detail")

# 用户询问行业排名
record_interaction("AI大模型", "industry", "asked_top10")

# 用户询问联系方式
record_interaction("宇树科技", "brand", "asked_contact")

# 用户对某条情报给出反馈
record_feedback(content_hash="vivo-X300发布会", feedback_type="positive", note="老板觉得创意很好")
record_feedback(content_hash="行业-半导体", feedback_type="negative", note="半导体和分众合作机会少")
```

## 日报板块说明

| 板块 | 推送频率 | 说明 |
|------|---------|------|
| 📋 客户新闻 | 每日 | 品牌监控 + 行业监控 |
| 💰 融资新闻 | 每周一、四 | 周一汇总上周四至周一融资，周四汇总周一至周四融资 |
