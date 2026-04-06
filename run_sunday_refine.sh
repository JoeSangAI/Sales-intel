#!/bin/zsh
# 白名单提纯 — 每周日运行
# 自动剔除低质量域名、添加高质量新域名

SALES_INTEL_DIR="/Users/Joe_1/Desktop/Development/sales-intel"
OUTPUT_DIR="/Users/Joe_1/Desktop/AI output/sales-intel"
LOG_FILE="$OUTPUT_DIR/whitelist_refine.log"
ERROR_LOG_FILE="$OUTPUT_DIR/whitelist_refine.error.log"

# 激活 conda 环境
source /Users/Joe_1/miniconda3/etc/profile.d/conda.sh
conda activate sales-intel

cd "$SALES_INTEL_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 白名单提纯开始 ===" >> "$LOG_FILE"

# 运行提纯器
python scripts/whitelist_refiner.py >> "$LOG_FILE" 2>> "$ERROR_LOG_FILE"
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 提纯完成" >> "$LOG_FILE"
    osascript -e "display notification \"白名单提纯完成，请查看报告\" with title \"Sales Intel 🔄\"" 2>/dev/null
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 提纯失败，退出码: $EXIT_CODE" >> "$ERROR_LOG_FILE"
    osascript -e "display notification \"白名单提纯失败，请检查日志\" with title \"Sales Intel ⚠️\"" 2>/dev/null
fi

exit $EXIT_CODE
