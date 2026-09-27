"""交期风险预警引擎 —— 把「排产结果」对照「订单交期」,自动算出超标/异常并分级预警。

看板铁律:排产不是排完就完,必须回头对交期。本模块只读、纯函数式,产出一份可直接
喂给大屏的风险报告:分级计数 + 预警明细列表,供 `/`(大屏)与 `/alerts/`(预警看板)渲染。

风险判定(每个未完成订单取「最严重」一档):

- **overdue 已逾期**(danger):交期日已过而订单未完成,或已排计划结束时刻晚于交期截止
  → 板上必须最刺眼的一档。
- **warning 临期紧张**(warning):已排且能按期,但结束距交期富余不足 `TIGHT_HOURS`;
  或待排订单交期已进入 `TIGHT_DAYS` 天内还没排产 → 再不动手就要超。
- **watch 需关注**(watch):待排订单交期在 `WATCH_DAYS` 天内、尚未安排 → 提前提醒。
- **abnormal 数据异常**(abnormal):状态为已排/在产却查无排产计划,或工时估算缺失(≤0)
  → 排产口径异常,先纠数据再谈交期。

计数里另给 `on_time`(已排且富余充足)做分母,方便大屏显示「N/M 可按期」。
"""

from dataclasses import dataclass, field
from datetime import datetime, time as dtime

from django.utils import timezone

from .models import Order

# ——— 阈值(可按厂情调整)———
TIGHT_HOURS = 24.0    # 已排:结束距交期富余小于它 → 临期紧张
TIGHT_DAYS = 2        # 待排:交期距今≤它天还没排 → 临期紧张
WATCH_DAYS = 6        # 待排:交期距今≤它天 → 需关注

# 严重度排序(数字越小越靠前/越严重),用于列表置顶与横幅取色。
LEVEL_RANK = {'overdue': 0, 'abnormal': 1, 'warning': 2, 'watch': 3, 'ok': 9}

# 大屏配色(与既有深色主题一致)。
LEVEL_META = {
    'overdue':  {'label': '已逾期',   'tag': 'danger',  'color': '#ff5a6a', 'icon': '⛔'},
    'abnormal': {'label': '数据异常', 'tag': 'abnormal', 'color': '#e0a13c', 'icon': '❗'},
    'warning':  {'label': '临期紧张', 'tag': 'warning', 'color': '#f0912f', 'icon': '⚠'},
    'watch':    {'label': '需关注',   'tag': 'watch',   'color': '#4fc3f7', 'icon': '👁'},
}


@dataclass
class Alert:
    order: object
    level: str          # overdue / abnormal / warning / watch
    reason: str         # 一句话说明为何预警
    due_dt: object      # 交期截止(当天 23:59)
    slack_hours: float  # 富余小时(负=超期时长);无计划时按天*24估算,异常为 None
    line_name: str = '' # 已排到的产线(如有)
    plan_end: object = None

    @property
    def meta(self):
        return LEVEL_META[self.level]

    @property
    def slack_text(self):
        """把富余/超期折算成「Xd Yh」人话。异常无计划则给占位。"""
        if self.slack_hours is None:
            return '—'
        neg = self.slack_hours < 0
        h = abs(self.slack_hours)
        d, hh = int(h // 24), int(round(h % 24))
        span = (f'{d}天' if d else '') + (f'{hh}小时' if (hh or not d) else '')
        return ('超期 ' if neg else '富余 ') + span


@dataclass
class RiskReport:
    now: object
    alerts: list = field(default_factory=list)
    considered: int = 0  # 纳入核对的未完成订单数

    @property
    def on_time(self):
        """无预警(可按期/交期尚宽)的订单数 = 核对总数 − 预警数。"""
        return max(self.considered - len(self.alerts), 0)

    @property
    def counts(self):
        c = {k: 0 for k in LEVEL_META}
        for a in self.alerts:
            c[a.level] += 1
        c['total'] = len(self.alerts)
        c['danger'] = c['overdue']                       # 最刺眼的红色总数
        c['on_time'] = self.on_time
        c['considered'] = self.considered
        return c

    @property
    def banner_level(self):
        """整体横幅取「现存最严重」一档;无预警返回 'ok'。"""
        best = 'ok'
        for a in self.alerts:
            if LEVEL_RANK[a.level] < LEVEL_RANK[best]:
                best = a.level
        return best

    @property
    def has_alerts(self):
        return bool(self.alerts)

    def top(self, n=6):
        return self.alerts[:n]


def _deadline(due_date):
    """交期截止时刻 = 交期当天 23:59:59.999999(带时区)。"""
    naive = datetime.combine(due_date, dtime.max)
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


def analyze_risk(now=None):
    """核对所有未完成订单(status != done)的排产结果与交期,产出分级预警报告。"""
    now = now or timezone.now()
    today = timezone.localdate()
    report = RiskReport(now=now)

    orders = (
        Order.objects.exclude(status='done')
        .prefetch_related('plans__line')
        .order_by('due_date')
    )

    for o in orders:
        report.considered += 1
        deadline = _deadline(o.due_date)
        # 该订单的最晚计划结束(排产结果)。
        plans = list(o.plans.all())
        latest = max((p for p in plans), key=lambda p: p.end_time, default=None)

        # —— 数据异常:排产态却无计划 / 工时缺失 ——
        if o.status in ('scheduled', 'producing') and not plans:
            report.alerts.append(Alert(
                order=o, level='abnormal', due_dt=deadline, slack_hours=None,
                reason='状态为「%s」却查无排产计划,排产口径异常' % o.get_status_display(),
            ))
            continue
        if (o.work_hours or 0) <= 0 and o.status == 'pending':
            report.alerts.append(Alert(
                order=o, level='abnormal', due_dt=deadline, slack_hours=None,
                reason='总工时估算缺失(≤0),无法评估交期风险',
            ))
            continue

        if latest is not None:
            # —— 已排:拿计划结束对交期 ——
            slack = (deadline - latest.end_time).total_seconds() / 3600.0
            line_name = latest.line.name if latest.line_id else ''
            if latest.end_time > deadline:
                report.alerts.append(Alert(
                    order=o, level='overdue', due_dt=deadline, slack_hours=slack,
                    line_name=line_name, plan_end=latest.end_time,
                    reason='排产计划结束(%s)晚于交期,预计无法按期交付'
                           % timezone.localtime(latest.end_time).strftime('%m-%d %H:%M'),
                ))
            elif slack < TIGHT_HOURS:
                report.alerts.append(Alert(
                    order=o, level='warning', due_dt=deadline, slack_hours=slack,
                    line_name=line_name, plan_end=latest.end_time,
                    reason='可按期,但距交期富余不足 %g 小时,几无缓冲' % TIGHT_HOURS,
                ))
            # 富余充足者不入预警(on_time 由差值派生)。
        else:
            # —— 待排(pending 且无计划):按交期临近度预警 ——
            days = (o.due_date - today).days
            slack = days * 24.0
            if days < 0:
                report.alerts.append(Alert(
                    order=o, level='overdue', due_dt=deadline, slack_hours=slack,
                    reason='交期已过 %d 天仍未排产' % (-days),
                ))
            elif days <= TIGHT_DAYS:
                report.alerts.append(Alert(
                    order=o, level='warning', due_dt=deadline, slack_hours=slack,
                    reason='交期仅剩 %d 天且尚未排产,需立即排产' % days,
                ))
            elif days <= WATCH_DAYS:
                report.alerts.append(Alert(
                    order=o, level='watch', due_dt=deadline, slack_hours=slack,
                    reason='交期 %d 天内、尚未排产,建议尽早安排' % days,
                ))
            # 交期尚远、暂不预警(on_time 由差值派生)。

    # 置顶排序:严重度 → 交期升序 → 富余升序(越紧越前)。
    report.alerts.sort(key=lambda a: (
        LEVEL_RANK[a.level], a.due_dt, a.slack_hours if a.slack_hours is not None else 1e9,
    ))
    return report
