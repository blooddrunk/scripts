#!/bin/sh
# quota-switch.sh — 守住 lightlayer-zh 的月流量：快用完时把 AI 流量（premium 组）改走 raksmart
#
# 配合 /etc/honk/config.dae 的组结构（2026-11-25 候选版）：
#   premium 是选择器，成员 lightlayer-zh / us / jp，默认 lightlayer-zh。
# 节点故障时的顺延由配置里的 final 自动完成；本脚本只管流量配额，不碰故障切换。
# komari 的 /api/recent 是月度真值（agent 带 --month-rotate <日>；公开接口，无需鉴权）。
# 切换用 clash API PUT /proxies/premium（选择持久化于 state/honk.db，跨 reload/重启保持）。
# 幂等；取数失败只记日志、不改选择，等下一次 cron 重试。DRY_RUN=1 时只打印、不切换。
#
# 配置文件默认 /root/honk-quota/config.sh，可用环境变量 QUOTA_CONF 覆盖。

. "${QUOTA_CONF:-/root/honk-quota/config.sh}"
DRY_RUN="${DRY_RUN:-0}"

log() {
    logger -t honk-quota "$1"
    [ "$DRY_RUN" = 1 ] && echo "honk-quota: $1"
    return 0
}
alert() {
    logger -t honk-quota -s "$1"
    [ "$DRY_RUN" = 1 ] && echo "honk-quota: $1" 1>&2
    return 0
}
ge() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a>=b)}'; }
lt() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a<b)}'; }
# 取 config.sh 里带前缀的变量，如 val UUID_ZH
val() { eval "printf '%s' \"\$$1\""; }

# ---- 某节点本月已用字节（/api/recent 最新样本的 totalUp + totalDown）----
month_used() {
    _rec=$(curl -sm 20 "$KOMARI_BASE/api/recent/$1" 2>/dev/null) || return 1
    _up=$(printf '%s' "$_rec" | grep -o '"totalUp":[0-9]*' | head -1 | cut -d: -f2)
    _down=$(printf '%s' "$_rec" | grep -o '"totalDown":[0-9]*' | head -1 | cut -d: -f2)
    [ -n "$_up" ] && [ -n "$_down" ] || return 1
    echo $((_up + _down))
}

# ---- 某节点的周期限额（字节），按 komari 里的节点名匹配 ----
node_limit() {
    _nodes=$(curl -sm 20 "$KOMARI_BASE/api/nodes" 2>/dev/null) || return 1
    _lim=$(printf '%s' "$_nodes" | grep -o "\"name\":\"$1\"[^}]*" \
        | grep -o '"traffic_limit":[0-9]*' | head -1 | cut -d: -f2)
    [ -n "$_lim" ] || return 1
    echo "$_lim"
}

# ---- 剩余率（百分比）。$1 是 config.sh 里的节点前缀：ZH / RAKSMART / BITSFLOW ----
remain_pct() {
    _used=$(month_used "$(val UUID_$1)") || return 1
    _limit=$(node_limit "$(val NODE_NAME_$1)") || return 1
    _off=0
    # 首周期补偿：agent 是月中才开始计数的，本周期此前的用量不计入；到期（首次重置）自动失效。
    # config.sh 里没给某台机器定义这两项时按 0 处理（即不补偿）。
    _expire=$(val "OFFSET_EXPIRE_$1")
    if [ -n "$_expire" ] && [ "$(date -u +%s)" -lt "$_expire" ]; then _off=$(val "OFFSET_$1"); fi
    awk -v u="$_used" -v o="$_off" -v l="$_limit" 'BEGIN{printf "%.1f", (l-u-o)*100/l}'
}

premium_now() {
    _r=$(curl -sm 5 -H "Authorization: Bearer $CLASH_SECRET" "$CLASH_API/proxies/premium" 2>/dev/null) || return 1
    printf '%s' "$_r" | grep -o '"now":"[^"]*"' | cut -d'"' -f4
}

premium_set() {
    curl -sm 5 -X PUT -H "Authorization: Bearer $CLASH_SECRET" \
        -d "{\"name\":\"$1\"}" "$CLASH_API/proxies/premium" -o /dev/null
}

# ================= 主流程 =================

# 1. 取数：全部拿到再决策，缺关键数据就不动现有的选择
CURRENT=$(premium_now) || { log "ERROR: read premium.now failed"; exit 1; }
ZH_REMAIN=$(remain_pct ZH) || { log "ERROR: fetch lightlayer-zh usage failed"; exit 1; }
WARN=""
for n in RAKSMART BITSFLOW; do
    if r=$(remain_pct "$n"); then
        eval "REMAIN_$n=$r"
        lt "$r" "$WARN_PCT" && WARN="$WARN $(val "NODE_NAME_$n")=${r}%"
    else
        log "WARN: fetch $n usage failed (alert-only, skipped)"
        eval "REMAIN_$n=?"
    fi
done
SUMMARY="lightlayer-zh remain ${ZH_REMAIN}%, raksmart ${REMAIN_RAKSMART}%, bitsflow ${REMAIN_BITSFLOW}%, T_HIGH=$T_HIGH T_LOW=$T_LOW"

# 2. premium 不是在 lightlayer-zh 与 us 之间（比如手动选了 jp）就不干预
case "$CURRENT" in
    "$GROUP_ZH"|"$GROUP_ZH_FALLBACK") ;;
    *)
        log "premium=$CURRENT 是手动选择，脚本不干预（$SUMMARY）"
        exit 0
        ;;
esac

# 3. 滞回决策：剩余 >= T_HIGH 用 lightlayer-zh；< T_LOW 改走 us；区间内维持现状
TARGET="$CURRENT"
if ge "$ZH_REMAIN" "$T_HIGH"; then
    TARGET="$GROUP_ZH"
elif lt "$ZH_REMAIN" "$T_LOW"; then
    TARGET="$GROUP_ZH_FALLBACK"
fi

# 4. 执行切换
if [ "$TARGET" != "$CURRENT" ]; then
    if [ "$DRY_RUN" = 1 ]; then
        log "DRY_RUN: premium $CURRENT -> $TARGET ($SUMMARY)"
    elif premium_set "$TARGET"; then
        log "premium: $CURRENT -> $TARGET ($SUMMARY)"
    else
        log "ERROR: premium_set $TARGET failed"
        exit 1
    fi
else
    log "hold premium=$CURRENT ($SUMMARY)"
fi

# 5. 日常机流量告警（日常流量无处可切，只提示）
[ -n "$WARN" ] && alert "daily VPS quota low:$WARN (WARN_PCT=$WARN_PCT)"
exit 0
