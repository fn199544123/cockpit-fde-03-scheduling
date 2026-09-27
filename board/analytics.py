"""产线负载率与产能利用分析引擎 —— 只读、纯函数,不落库。

从两个互补的角度衡量每条产线「忙不忙、饱不饱和」:

1. **排产负载率(Schedule Load Rate)** —— 面向未来的「订到几分满」。
   在未来一段时间窗(默认 7 天)内,该产线**已排产/在产计划**占用的工时,
   对比该产线在同一窗口的**可排产能工时**(= 窗口天数 × 每日可排工时 DAILY_WORK_HOURS)。
   负载率 = 占用工时 / 可用工时。反映排产是否把线排满、是否过载。
     - < 40%  空闲(可承接更多订单)
     - 40–85% 正常
     - 85–100% 繁忙(接近满负荷)
     - > 100%  超载(排产已超出可用工时,交期风险高)

2. **产能利用率(Capacity Utilization)** —— 面向实绩的「产能用得够不够」。
   基于近一段时间(默认与负载窗口同长)的**真实报工产出**,算该产线的
   实际日均产出,对比其额定「日产能」。利用率 = 实际日均产出 / 日产能。
   反映额定产能有没有被真正兑现(欠饱和 / 饱和 / 超产)。

窗口天数、每日可排工时集中在本文件顶部常量,可调。计算全部只读,适合大屏轮询。
"""

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta

from django.db.models import Sum
from django.utils import timezone

from .models import Line, Plan, ProductionLog

# —— 可调参数 ——
# 每条产线「每日可排产能工时」:演示按两班有效工时估算(可按 3 班调到更高)。
DAILY_WORK_HOURS = 20.0
# 默认分析时间窗(天)。
DEFAULT_DAYS = 7
# 分析窗口护栏。
MIN_DAYS, MAX_DAYS = 1, 60

# 负载率分档阈值(%)。
LOAD_IDLE = 40.0     # 以下:空闲
LOAD_BUSY = 85.0     # 以上(<100):繁忙
LOAD_OVER = 100.0    # 以上:超载

# 负载率分档元信息(用于大屏配色/标签)。
LOAD_BANDS = {
    'idle':   {'label': '空闲',   'color': '#4a6f9c', 'tip': '可承接更多订单'},
    'normal': {'label': '正常',   'color': '#5fd39a', 'tip': '负荷健康'},
    'busy':   {'label': '繁忙',   'color': '#f0b072', 'tip': '接近满负荷'},
    'over':   {'label': '超载',   'color': '#ff5a6a', 'tip': '排产已超可用工时,交期风险高'},
}


def _classify_load(rate):
    if rate > LOAD_OVER:
        return 'over'
    if rate >= LOAD_BUSY:
        return 'busy'
    if rate < LOAD_IDLE:
        return 'idle'
    return 'normal'


def _classify_cap(util):
    """产能利用率分档(基于实绩产出)。"""
    if util is None:
        return 'nodata'
    if util > 100.0:
        return 'over'      # 超产(报工超过额定日产能)
    if util >= 85.0:
        return 'full'      # 饱和
    if util < 40.0:
        return 'low'       # 明显欠饱和
    return 'mid'           # 一般


CAP_BANDS = {
    'over':   {'label': '超产',   'color': '#7c8cff'},
    'full':   {'label': '饱和',   'color': '#5fd39a'},
    'mid':    {'label': '一般',   'color': '#f0b072'},
    'low':    {'label': '欠饱和', 'color': '#4a6f9c'},
    'nodata': {'label': '无报工', 'color': '#556'},
}


@dataclass
class LineUtil:
    line: object
    # 负载
    load_hours: float
    avail_hours: float
    load_rate: float           # %
    load_band: str
    plan_count: int
    # 产能利用
    out_total: int             # 窗口内实际产出(件)
    out_ok: int
    out_ng: int
    avg_daily_out: float       # 件/天
    capacity: int              # 额定日产能
    cap_util: float or None    # %
    cap_band: str
    yield_rate: float or None  # 良品率 %

    @property
    def free_hours(self):
        return round(max(self.avail_hours - self.load_hours, 0), 1)


@dataclass
class UtilReport:
    now: object
    days: int
    win_start: object
    win_end: object
    daily_hours: float
    lines: list = field(default_factory=list)

    # —— 汇总 ——
    @property
    def avg_load(self):
        rates = [l.load_rate for l in self.lines]
        return round(sum(rates) / len(rates), 1) if rates else 0.0

    @property
    def avg_cap_util(self):
        vals = [l.cap_util for l in self.lines if l.cap_util is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    @property
    def total_load_hours(self):
        return round(sum(l.load_hours for l in self.lines), 1)

    @property
    def total_avail_hours(self):
        return round(sum(l.avail_hours for l in self.lines), 1)

    @property
    def overall_load(self):
        return round(self.total_load_hours * 100.0 / self.total_avail_hours, 1) if self.total_avail_hours else 0.0

    @property
    def overloaded(self):
        return [l for l in self.lines if l.load_band == 'over']

    @property
    def idle(self):
        return [l for l in self.lines if l.load_band == 'idle']

    @property
    def bottleneck(self):
        """瓶颈线 = 负载率最高的那条。"""
        return max(self.lines, key=lambda l: l.load_rate) if self.lines else None

    @property
    def most_idle(self):
        return min(self.lines, key=lambda l: l.load_rate) if self.lines else None

    def band_counts(self):
        c = {k: 0 for k in LOAD_BANDS}
        for l in self.lines:
            c[l.load_band] += 1
        return c


def _overlap_hours(p_start, p_end, w_start, w_end):
    """计划 [p_start,p_end] 落在窗口 [w_start,w_end] 内的重叠工时。"""
    lo = max(p_start, w_start)
    hi = min(p_end, w_end)
    sec = (hi - lo).total_seconds()
    return sec / 3600.0 if sec > 0 else 0.0


def analyze_utilization(now=None, days=DEFAULT_DAYS, daily_hours=DAILY_WORK_HOURS):
    """产线负载率 + 产能利用分析。返回 UtilReport(只读)。"""
    now = now or timezone.now()
    days = max(MIN_DAYS, min(MAX_DAYS, int(days)))

    # 负载窗口:从此刻起未来 days 天(面向未来的排产占用)。
    win_start = now
    win_end = now + timedelta(days=days)
    avail_hours = round(days * daily_hours, 1)

    # 产能窗口:近 days 天的报工实绩(面向过去的产出)。
    today = timezone.localdate()
    out_start = today - timedelta(days=days - 1)

    lines = []
    for line in Line.objects.filter(is_active=True).order_by('code'):
        # —— 负载:未完成计划在窗口内的占用工时 ——
        plans = (
            Plan.objects.filter(line=line, end_time__gt=win_start, start_time__lt=win_end)
            .exclude(progress=100)
        )
        load_hours, plan_count = 0.0, 0
        for p in plans:
            h = _overlap_hours(p.start_time, p.end_time, win_start, win_end)
            if h > 0:
                load_hours += h
                plan_count += 1
        load_hours = round(load_hours, 1)
        load_rate = round(load_hours * 100.0 / avail_hours, 1) if avail_hours else 0.0

        # —— 产能利用:近 days 天报工产出 vs 额定日产能 ——
        lo = (
            ProductionLog.objects.filter(line=line, logged_at__date__gte=out_start)
            .aggregate(ok=Sum('qty_ok'), ng=Sum('qty_ng'))
        )
        ok, ng = lo['ok'] or 0, lo['ng'] or 0
        out_total = ok + ng
        avg_daily = round(out_total / days, 1)
        capacity = line.capacity or 0
        cap_util = round(avg_daily * 100.0 / capacity, 1) if capacity else None
        yield_rate = round(ok * 100.0 / out_total, 1) if out_total else None

        lines.append(LineUtil(
            line=line,
            load_hours=load_hours, avail_hours=avail_hours,
            load_rate=load_rate, load_band=_classify_load(load_rate),
            plan_count=plan_count,
            out_total=out_total, out_ok=ok, out_ng=ng,
            avg_daily_out=avg_daily, capacity=capacity,
            cap_util=cap_util, cap_band=_classify_cap(cap_util),
            yield_rate=yield_rate,
        ))

    return UtilReport(
        now=now, days=days,
        win_start=win_start, win_end=win_end,
        daily_hours=daily_hours, lines=lines,
    )
