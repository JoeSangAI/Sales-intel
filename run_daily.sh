#!/bin/zsh
# Sales Intel 每日定时任务脚本
# 由 launchd 每天 8:00 & 12:00 触发 + watchdog 周期性检查补跑

SALES_INTEL_DIR="/Users/Joe_1/Desktop/Vibe Working/tools/sales-intel"
OUTPUT_DIR="/Users/Joe_1/Desktop/AI output/sales intel"
FLAG_FILE="$OUTPUT_DIR/.last_run_date"
LOG_FILE="$OUTPUT_DIR/launchd.log"
ERROR_LOG_FILE="$OUTPUT_DIR/launchd.error.log"

TODAY=$(date '+%Y-%m-%d')

# 检查今天是否已经跑过
if [ -f "$FLAG_FILE" ]; then
    LAST_RUN=$(cat "$FLAG_FILE")
    if [ "$LAST_RUN" = "$TODAY" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 今日已完成，跳过" >> "$LOG_FILE"
        exit 0
    fi
fi

# 激活 conda 环境
source /Users/Joe_1/miniconda3/etc/profile.d/conda.sh
conda activate sales-intel

cd "$SALES_INTEL_DIR"

# 通知：任务开始
osascript -e "display notification \"销售情报开始跑所有人的档案了\" with title \"Sales Intel 🔍\"" 2>/dev/null

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 销售情报任务开始 ===" >> "$LOG_FILE"

# 执行主脚本
python scripts/main.py --profile-all >> "$LOG_FILE" 2>> "$ERROR_LOG_FILE"
EXIT_CODE=$?

# 标记今天已完成
echo "$TODAY" > "$FLAG_FILE"

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 完成" >> "$LOG_FILE"
    osascript -e "display notification \"销售情报跑完了，日报已生成\" with title \"Sales Intel ✅\"" 2>/dev/null
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 失败，退出码: $EXIT_CODE" >> "$ERROR_LOG_FILE"
    osascript -e "display notification \"销售情报失败，请检查日志\" with title \"Sales Intel ⚠️\"" 2>/dev/null
fi

exit $EXIT_CODE
