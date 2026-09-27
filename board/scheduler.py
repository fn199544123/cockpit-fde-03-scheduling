"""智能排产引擎 —— 按产线负载与交期,把队列里的订单一键自动排到甘特计划上。

核心策略(贪心列表调度 + 负载均衡 + 交期感知):

1. **排序**:待排订单按「优先级降序 → 交期升序 → 创建时间」入队,越紧急/越早交期越先排。
2. **产线负载**:每条产线维护一个「可用光标」(next_free),初值 = max(此刻, 该线现存计划的最晚结束)。
   —— 已在产/已排的计划视为占用,新单顺延其后,绝不重叠。
3. **产能加权工期**:订单基准工时按产线日产能折算实际耗时,产能越高的线做得越快
   (duration = work_hours × 基准产能 / 该线产能)。
4. **选线**:为每个订单挑「做完最早」的那条线(min(光标 + 该线工期)),天然把负载摊平、压缩总工期。
5. **交期核对**:计划结束晚于交期则标记「预计超期」,给出提前量/超期时长,供看板预警。

纯函数式、无第三方依赖;结果落库为 Plan,并把订单状态由 pending → scheduled。
"""

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta

from django.db import transaction
from django.utils import timezone

from .models import Line, Order, Plan

# 折算工期用的基准日产能:产能等于它的产线为「1 倍速」。
BASELINE_CAPACITY = 100.0
# 单条订单允许的最短/最长工期护栏(小时),避免异常数据把甘特拉爆。
MIN_HOURS = 0.5
MAX_HOURS = 240.0


@dataclass
class LineState:
    line: Line
    next_free: object          # datetime:该线下一个可用时刻
    planned_hours: float = 0.0  # 本轮新排到该线的总工时
    plan_count: int = 0

    def duration_for(self, order):
        """订单在本线的实际工期(小时),按产能折算并夹到护栏区间。"""
        cap = self.line.capacity or BASELINE_CAPACITY
        base = order.work_hours or (order.quantity * 0.1)
        hours = base * (BASELINE_CAPACITY / cap)
        return max(MIN_HOURS, min(MAX_HOURS, hours))


@dataclass
class ScheduleResult:
    scheduled: int = 0
    skipped_no_line: int = 0
    on_time: int = 0
    late: int = 0
    plans: list = field(default_factory=list)
    details: list = field(default_factory=list)  # 每单一行:订单/产线/起止/是否超期

    @property
    def ok(self):
        return self.scheduled > 0


def _line_states(lines, anchor, respect_existing=True):
    """构造各产线的初始负载光标。已有计划(在产/已排)视为占用,新单顺延其后。"""
    states = []
    for line in lines:
        cursor = anchor
        if respect_existing:
            last = (
                Plan.objects.filter(line=line, end_time__gt=anchor)
                .order_by('-end_time').values_list('end_time', flat=True).first()
            )
            if last:
                cursor = max(cursor, last)
        states.append(LineState(line=line, next_free=cursor))
    return states


def pending_orders():
    """排产队列:待排产订单(优先级降序、交期升序)。"""
    return list(
        Order.objects.filter(status='pending').order_by(
            '-priority', 'due_date', 'created_at'
        )
    )


@transaction.atomic
def auto_schedule(clear_pending=True, respect_existing=True):
    """一键自动排产。

    - clear_pending:先清掉待排订单的历史计划,避免重复排(只清 pending 单,不动在产/已完成)。
    - respect_existing:新计划在现存计划之后顺延,保证同线不重叠。
    返回 ScheduleResult。
    """
    result = ScheduleResult()

    lines = list(Line.objects.filter(is_active=True).order_by('code'))
    orders = pending_orders()
    if not lines or not orders:
        return result

    now = timezone.now()
    # 对齐到下一个整点,甘特更好看。
    anchor = (now + timedelta(minutes=59)).replace(minute=0, second=0, microsecond=0)

    if clear_pending:
        Plan.objects.filter(order__in=orders).delete()

    states = _line_states(lines, anchor, respect_existing=respect_existing)

    for order in orders:
        # 选「做完最早」的产线(负载均衡的核心)。
        best = min(states, key=lambda s: s.next_free + timedelta(hours=s.duration_for(order)))
        dur = best.duration_for(order)
        start = best.next_free
        end = start + timedelta(hours=dur)

        plan = Plan.objects.create(
            order=order, line=best.line,
            process=best.line.processes.order_by('seq').first(),
            start_time=start, end_time=end, progress=0,
        )
        order.status = 'scheduled'
        order.save(update_fields=['status'])

        # 推进该线光标。
        best.next_free = end
        best.planned_hours += dur
        best.plan_count += 1

        # 交期核对(交期当天 23:59 为最后期限)。
        due_naive = datetime.combine(order.due_date, dtime.max)
        due_dt = timezone.make_aware(due_naive) if timezone.is_naive(due_naive) else due_naive
        late = end > due_dt
        if late:
            result.late += 1
        else:
            result.on_time += 1

        slack_hours = (due_dt - end).total_seconds() / 3600.0
        result.scheduled += 1
        result.plans.append(plan)
        result.details.append({
            'order_no': order.order_no,
            'product': order.product,
            'line': best.line.name,
            'start': start,
            'end': end,
            'hours': round(dur, 1),
            'due': order.due_date,
            'late': late,
            'slack_hours': round(slack_hours, 1),
        })

    return result
