#!/bin/bash
# 停止销售情报定时任务
launchctl unload ~/Library/LaunchAgents/com.joe.sales-intel.daily.plist 2>/dev/null
echo "Sales Intel 定时任务已停止"
