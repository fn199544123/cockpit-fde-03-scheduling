from django.contrib import messages
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import LineForm, OrderForm, ProcessForm, ProductionLogForm
from .models import Line, Order, Plan, Process, ProductionLog


def board(request):
    """大屏炫酷看板首页:核心指标 KPI + 交期风险 + 产线负荷 + 订单结构 + 实时报工。

    所有数字均来自真实数据库(订单/排产计划/报工记录),适合投屏/录屏演示。
    """
    from django.db.models import Sum
    from django.utils import timezone as tz
    from .risk import analyze_risk

    now = tz.now()
    today = tz.localdate()
    report = getattr(request, '_risk_report', None) or analyze_risk(now)

    # —— 订单结构(按状态)——
    order_total = Order.objects.count()
    status_meta = [
        ('pending', '待排产', '#4fc3f7'),
        ('scheduled', '已排产', '#7c8cff'),
        ('producing', '生产中', '#5fd39a'),
        ('done', '已完成', '#5a6f86'),
    ]
    status_rows, acc = [], 0.0
    for code, label, color in status_meta:
        n = Order.objects.filter(status=code).count()
        pct = round(n * 100.0 / order_total, 1) if order_total else 0
        start = acc
        acc += pct
        status_rows.append({
            'code': code, 'label': label, 'color': color, 'n': n,
            'pct': pct, 'start': round(start, 2), 'stop': round(acc, 2),
        })
    # 环形图的 conic-gradient 色段串;无订单时给一圈灰。
    if order_total and acc > 0:
        segs = [f"{r['color']} {r['start']}% {r['stop']}%" for r in status_rows if r['pct'] > 0]
        donut_css = ', '.join(segs)
    else:
        donut_css = '#1f3a52 0% 100%'

    # —— 报工产出(全量 + 今日)——
    logs = ProductionLog.objects
    agg = logs.aggregate(ok=Sum('qty_ok'), ng=Sum('qty_ng'))
    total_ok, total_ng = agg['ok'] or 0, agg['ng'] or 0
    total_out = total_ok + total_ng
    yield_rate = round(total_ok * 100.0 / total_out, 1) if total_out else None

    tagg = logs.filter(logged_at__date=today).aggregate(ok=Sum('qty_ok'), ng=Sum('qty_ng'))
    today_ok, today_ng = tagg['ok'] or 0, tagg['ng'] or 0
    today_out = today_ok + today_ng

    # —— 班次产出对比 ——
    shift_rows = []
    for code, label in ProductionLog.SHIFT_CHOICES:
        s = logs.filter(shift=code).aggregate(ok=Sum('qty_ok'), ng=Sum('qty_ng'))
        ok, ng = s['ok'] or 0, s['ng'] or 0
        shift_rows.append({'label': label, 'ok': ok, 'ng': ng, 'total': ok + ng})
    shift_max = max((x['total'] for x in shift_rows), default=0) or 1
    for x in shift_rows:
        x['pct'] = round(x['total'] * 100.0 / shift_max, 1)

    # —— 各产线:负荷(未完成计划工时)+ 累计产出 ——
    line_rows = []
    for line in Line.objects.all().order_by('code'):
        active_plans = Plan.objects.filter(line=line, end_time__gt=now).exclude(progress=100)
        load_hours = round(sum(p.duration_hours for p in active_plans), 1)
        lo = logs.filter(line=line).aggregate(ok=Sum('qty_ok'), ng=Sum('qty_ng'))
        ok, ng = lo['ok'] or 0, lo['ng'] or 0
        out = ok + ng
        line_rows.append({
            'line': line, 'load_hours': load_hours, 'plan_count': active_plans.count(),
            'out': out, 'ok': ok, 'ng': ng,
            'yield': round(ok * 100.0 / out, 1) if out else None,
            'capacity': line.capacity, 'is_active': line.is_active,
        })
    max_load = max((r['load_hours'] for r in line_rows), default=0) or 1
    max_out = max((r['out'] for r in line_rows), default=0) or 1
    for r in line_rows:
        r['load_pct'] = round(r['load_hours'] * 100.0 / max_load, 1)
        r['out_pct'] = round(r['out'] * 100.0 / max_out, 1)

    # ================= 数据可视化(手写 SVG,前端从下列 JSON 绘制)=================
    from datetime import timedelta
    from django.db.models.functions import TruncDate

    # —— 趋势曲线:近 14 天每日产出 + 良品率 ——
    span_days = 14
    start_day = today - timedelta(days=span_days - 1)
    per_day = (
        logs.filter(logged_at__date__gte=start_day)
        .annotate(d=TruncDate('logged_at'))
        .values('d')
        .annotate(ok=Sum('qty_ok'), ng=Sum('qty_ng'))
    )
    by_date = {row['d']: (row['ok'] or 0, row['ng'] or 0) for row in per_day}
    viz_trend = []
    for i in range(span_days):
        d = start_day + timedelta(days=i)
        ok, ng = by_date.get(d, (0, 0))
        tot = ok + ng
        viz_trend.append({
            'label': d.strftime('%m-%d'),
            'ok': ok, 'ng': ng, 'out': tot,
            'yield': round(ok * 100.0 / tot, 1) if tot else None,
        })

    # —— 结构占比环图:各产线累计产出占比 ——
    viz_palette = ['#4fc3f7', '#7c8cff', '#5fd39a', '#f0b072',
                   '#ff8a9c', '#b39ddb', '#4dd0e1', '#ffd166']
    donut_segs, di = [], 0
    for r in line_rows:
        if r['out'] <= 0:
            continue
        donut_segs.append({
            'label': r['line'].name,
            'code': r['line'].code,
            'value': r['out'],
            'color': viz_palette[di % len(viz_palette)],
        })
        di += 1
    viz_donut = {'total': total_out, 'segments': donut_segs}

    # —— 对比柱状:班次产出(合格 / 不良 分组)——
    viz_bars = {
        'groups': [
            {'label': s['label'], 'ok': s['ok'], 'ng': s['ng']}
            for s in shift_rows
        ],
    }

    ctx = {
        'now': now,
        # 顶部 KPI
        'line_count': Line.objects.count(),
        'line_active': Line.objects.filter(is_active=True).count(),
        'process_count': Process.objects.count(),
        'order_count': order_total,
        'plan_count': Plan.objects.count(),
        'pending_count': Order.objects.filter(status='pending').count(),
        'producing_count': Order.objects.filter(status='producing').count(),
        'log_count': logs.count(),
        'total_ok': total_ok, 'total_ng': total_ng, 'total_out': total_out,
        'yield_rate': yield_rate,
        'today_ok': today_ok, 'today_ng': today_ng, 'today_out': today_out,
        # 面板
        'status_rows': status_rows,
        'donut_css': donut_css,
        'shift_rows': shift_rows,
        'line_rows': line_rows,
        'viz_trend': viz_trend,
        'viz_donut': viz_donut,
        'viz_bars': viz_bars,
        'lines': Line.objects.all(),
        'recent_logs': logs.select_related('line', 'process', 'order')[:10],
        'risk': report,
        'risk_counts': report.counts,
        'risk_top': report.top(5),
    }
    return render(request, 'board/board.html', ctx)


def alerts(request):
    """交期风险预警看板:排产结果对照交期,分级计数 + 全量预警明细。"""
    from .risk import analyze_risk, LEVEL_META, LEVEL_RANK

    report = getattr(request, '_risk_report', None) or analyze_risk()
    # 按等级分组,供页面分区展示(严重度顺序)。
    groups = []
    by_level = {}
    for a in report.alerts:
        by_level.setdefault(a.level, []).append(a)
    for level in sorted(by_level, key=lambda l: LEVEL_RANK[l]):
        groups.append({
            'level': level,
            'meta': LEVEL_META[level],
            'items': by_level[level],
        })
    ctx = {
        'risk': report,
        'risk_counts': report.counts,
        'groups': groups,
    }
    return render(request, 'board/alerts.html', ctx)


# ---------------------------------------------------------------------------
# 产线负载率与产能利用分析 —— 未来窗口的排产负载率 + 近窗口报工的产能利用率。
# ---------------------------------------------------------------------------

def utilization(request):
    """产线负载率与产能利用分析看板。

    - 负载率:未来 days 天,各产线已排/在产计划占用工时 vs 可排产能工时。
    - 产能利用率:近 days 天真实报工产出的日均值 vs 额定日产能。
    时间窗可用 `?days=N` 调整(1~60,默认 7)。
    """
    from django.utils import timezone as tz
    from .analytics import analyze_utilization, LOAD_BANDS, CAP_BANDS, DEFAULT_DAYS

    try:
        days = int(request.GET.get('days', DEFAULT_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_DAYS

    now = tz.now()
    report = analyze_utilization(now=now, days=days)

    # 供模板画横条:负载率条宽按「相对满载」封顶 100%,超载部分单独标红。
    rows = []
    for lu in report.lines:
        load_fill = min(lu.load_rate, 100.0)
        over_fill = max(lu.load_rate - 100.0, 0.0)
        rows.append({
            'u': lu,
            'load_meta': LOAD_BANDS[lu.load_band],
            'cap_meta': CAP_BANDS[lu.cap_band],
            'load_fill': round(load_fill, 1),
            'over_fill': round(min(over_fill, 60.0), 1),  # 超载条最多再画 60% 宽,防溢出
            'cap_fill': round(min(lu.cap_util, 120.0), 1) if lu.cap_util is not None else 0,
        })

    counts = report.band_counts()
    legend = [
        {'label': m['label'], 'color': m['color'], 'count': counts[key]}
        for key, m in LOAD_BANDS.items()
    ]

    ctx = {
        'report': report,
        'rows': rows,
        'days': report.days,
        'legend': legend,
        'day_options': [3, 7, 14, 30],
        'now': now,
    }
    return render(request, 'board/utilization.html', ctx)


# ---------------------------------------------------------------------------
# 基础台账(主数据)CRUD —— 三类实体共用一套注册表 + 通用视图,避免重复。
# ---------------------------------------------------------------------------

# 每个实体:模型、表单、中文名、列表列(取值表达式)、按钮/标题用文案。
REGISTRY = {
    'line': {
        'model': Line,
        'form': LineForm,
        'label': '产线',
        'icon': '🏭',
        'order_by': ['code'],
        'search': ['name', 'code', 'foreman'],
        'columns': [
            ('编号', lambda o: o.code),
            ('名称', lambda o: o.name),
            ('线长', lambda o: o.foreman or '—'),
            ('日产能', lambda o: f'{o.capacity} 件'),
            ('状态', lambda o: '启用' if o.is_active else '停用'),
            ('备注', lambda o: o.remark or '—'),
        ],
    },
    'process': {
        'model': Process,
        'form': ProcessForm,
        'label': '工序',
        'icon': '🔧',
        'order_by': ['line__code', 'seq'],
        'search': ['name', 'code'],
        'columns': [
            ('编号', lambda o: o.code),
            ('名称', lambda o: o.name),
            ('所属产线', lambda o: o.line.name if o.line else '—'),
            ('顺序', lambda o: o.seq),
            ('标准工时', lambda o: f'{o.std_hours} h/件'),
        ],
    },
    'order': {
        'model': Order,
        'form': OrderForm,
        'label': '订单',
        'icon': '📦',
        'order_by': ['-priority', 'due_date'],
        'search': ['order_no', 'product', 'customer'],
        'columns': [
            ('订单号', lambda o: o.order_no),
            ('产品', lambda o: o.product),
            ('客户', lambda o: o.customer or '—'),
            ('数量', lambda o: f'{o.quantity} 件'),
            ('总工时', lambda o: f'{o.work_hours} h'),
            ('交期', lambda o: o.due_date.strftime('%Y-%m-%d')),
            ('优先级', lambda o: o.get_priority_display()),
            ('状态', lambda o: o.get_status_display()),
        ],
    },
    'report': {
        'model': ProductionLog,
        'form': ProductionLogForm,
        'label': '报工记录',
        'icon': '📝',
        'order_by': ['-logged_at', '-id'],
        'search': ['operator', 'remark', 'order__order_no', 'line__name'],
        'columns': [
            ('时间', lambda o: o.logged_at.strftime('%m-%d %H:%M')),
            ('产线', lambda o: o.line.name),
            ('工序', lambda o: o.process.name if o.process else '—'),
            ('订单', lambda o: o.order.order_no if o.order else '—'),
            ('班次', lambda o: o.get_shift_display()),
            ('合格', lambda o: f'{o.qty_ok} 件'),
            ('不良', lambda o: f'{o.qty_ng} 件'),
            ('良品率', lambda o: f'{o.yield_rate}%' if o.yield_rate is not None else '—'),
            ('报工人', lambda o: o.operator or '—'),
        ],
    },
}


def _cfg(entity):
    cfg = REGISTRY.get(entity)
    if cfg is None:
        from django.http import Http404
        raise Http404('未知台账类型')
    return cfg


def ledger_home(request):
    """台账总览:列出各主数据的数量与入口。"""
    items = []
    for entity, cfg in REGISTRY.items():
        items.append({
            'entity': entity,
            'label': cfg['label'],
            'icon': cfg['icon'],
            'count': cfg['model'].objects.count(),
        })
    return render(request, 'board/ledger_home.html', {'items': items})


def ledger_list(request, entity):
    cfg = _cfg(entity)
    qs = cfg['model'].objects.all().order_by(*cfg['order_by'])
    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q
        cond = Q()
        for f in cfg['search']:
            cond |= Q(**{f'{f}__icontains': q})
        qs = qs.filter(cond)

    rows = []
    for obj in qs:
        rows.append({
            'pk': obj.pk,
            'cells': [col[1](obj) for col in cfg['columns']],
        })

    ctx = {
        'entity': entity,
        'label': cfg['label'],
        'icon': cfg['icon'],
        'headers': [c[0] for c in cfg['columns']],
        'rows': rows,
        'q': q,
        'total': qs.count(),
    }
    return render(request, 'board/ledger_list.html', ctx)


def ledger_edit(request, entity, pk=None):
    """新增(pk=None)或编辑一条主数据。"""
    cfg = _cfg(entity)
    obj = get_object_or_404(cfg['model'], pk=pk) if pk else None
    FormClass = cfg['form']

    if request.method == 'POST':
        form = FormClass(request.POST, instance=obj)
        if form.is_valid():
            saved = form.save()
            messages.success(
                request,
                f'{cfg["label"]}「{saved}」已{"更新" if pk else "新增"}。',
            )
            return redirect(reverse('board:ledger_list', args=[entity]))
    else:
        form = FormClass(instance=obj)

    ctx = {
        'entity': entity,
        'label': cfg['label'],
        'icon': cfg['icon'],
        'form': form,
        'is_edit': bool(pk),
        'obj': obj,
    }
    return render(request, 'board/ledger_form.html', ctx)


def ledger_delete(request, entity, pk):
    """删除一条主数据(仅接受 POST,带确认)。"""
    cfg = _cfg(entity)
    obj = get_object_or_404(cfg['model'], pk=pk)
    if request.method == 'POST':
        name = str(obj)
        try:
            obj.delete()
            messages.success(request, f'{cfg["label"]}「{name}」已删除。')
        except ProtectedError:
            messages.error(request, f'{cfg["label"]}「{name}」被排产计划等数据引用,无法删除。')
        return redirect(reverse('board:ledger_list', args=[entity]))
    # GET:确认页
    return render(request, 'board/ledger_confirm_delete.html', {
        'entity': entity,
        'label': cfg['label'],
        'icon': cfg['icon'],
        'obj': obj,
    })


# ---------------------------------------------------------------------------
# 数据采集 —— 车间快速报工。为大屏/触屏场景优化:选产线→录产出→提交即落库,
# 提交后保留「产线/班次/报工人」上下文,方便一条接一条连续打卡上报。
# ---------------------------------------------------------------------------

def collect(request):
    """快速报工采集页(核心录入入口)。"""
    from django.db.models import Sum
    from django.utils import timezone

    if request.method == 'POST':
        form = ProductionLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.source = 'quick'
            log.save()
            messages.success(
                request,
                f'已采集:{log.line.name} {log.get_shift_display()} '
                f'合格 {log.qty_ok} / 不良 {log.qty_ng} 件。',
            )
            # 保留上下文,连续录入下一条
            keep = f'?line={log.line_id}&shift={log.shift}'
            if log.operator:
                keep += f'&operator={log.operator}'
            return redirect(reverse('board:collect') + keep)
        # 校验失败落到下方,复用同一 form 显示错误
    else:
        # 用 querystring 里的上下文预填,便于连续录入
        initial = {'logged_at': timezone.now().strftime('%Y-%m-%dT%H:%M')}
        for key in ('line', 'shift', 'operator'):
            val = request.GET.get(key)
            if val:
                initial[key] = val
        form = ProductionLogForm(initial=initial)

    today = timezone.localdate()
    today_qs = ProductionLog.objects.filter(logged_at__date=today)
    agg = today_qs.aggregate(ok=Sum('qty_ok'), ng=Sum('qty_ng'))
    today_ok = agg['ok'] or 0
    today_ng = agg['ng'] or 0
    today_total = today_ok + today_ng

    ctx = {
        'form': form,
        'recent': ProductionLog.objects.select_related('line', 'process', 'order')[:12],
        'has_lines': Line.objects.exists(),
        'stats': {
            'today_ok': today_ok,
            'today_ng': today_ng,
            'today_count': today_qs.count(),
            'today_yield': round(today_ok * 100.0 / today_total, 1) if today_total else None,
            'total': ProductionLog.objects.count(),
        },
    }
    return render(request, 'board/collect.html', ctx)


# ---------------------------------------------------------------------------
# 智能排产 —— 订单进队列,按产线负载与交期一键自动排产,生成甘特计划。
# ---------------------------------------------------------------------------

def _gantt(plans, now=None):
    """把一批 Plan 组织成按产线分组的甘特数据。

    统一时间窗 = [min(start, now), max(end)],每个条带算出 left%/width% 便于纯 CSS 渲染。
    返回 (lanes, axis, window) —— lanes 按产线,axis 为日期刻度。
    """
    from django.utils import timezone as tz

    now = now or tz.now()
    plans = list(plans)
    if not plans:
        return [], [], None

    starts = [p.start_time for p in plans]
    ends = [p.end_time for p in plans]
    win_start = min(min(starts), now)
    win_end = max(ends)
    # 至少留 6 小时窗宽,避免除零/太窄。
    total = max((win_end - win_start).total_seconds(), 6 * 3600)

    def pct(dt):
        return round((dt - win_start).total_seconds() / total * 100, 3)

    # 颜色按优先级区分。
    prio_color = {1: '#4a6070', 2: '#1565c0', 3: '#b26a00', 4: '#a52233'}

    by_line = {}
    for p in plans:
        by_line.setdefault(p.line_id, {'line': p.line, 'bars': []})
        late = p.end_time.date() > p.order.due_date
        by_line[p.line_id]['bars'].append({
            'plan': p,
            'order_no': p.order.order_no,
            'product': p.order.product,
            'left': pct(p.start_time),
            'width': max(pct(p.end_time) - pct(p.start_time), 1.2),
            'color': prio_color.get(p.order.priority, '#1565c0'),
            'priority': p.order.get_priority_display(),
            'progress': p.progress,
            'hours': round(p.duration_hours, 1),
            'start': p.start_time,
            'end': p.end_time,
            'due': p.order.due_date,
            'late': late,
        })

    lanes = sorted(by_line.values(), key=lambda d: d['line'].code)

    # 日期刻度(按天)。
    from datetime import timedelta
    axis = []
    day = win_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= win_end:
        left = (day - win_start).total_seconds() / total * 100
        if -1 <= left <= 101:
            axis.append({'label': day.strftime('%m-%d'), 'left': round(max(left, 0), 3)})
        day += timedelta(days=1)

    now_left = pct(now)
    window = {
        'start': win_start, 'end': win_end,
        'now_left': now_left if 0 <= now_left <= 100 else None,
    }
    return lanes, axis, window


def schedule(request):
    """排产工作台:待排队列 + 各产线负载 + 甘特计划;一键自动排产。"""
    from django.db.models import Count, Sum
    from django.utils import timezone as tz
    from .scheduler import auto_schedule, pending_orders

    if request.method == 'POST':
        result = auto_schedule()
        if not result.scheduled:
            if not Line.objects.filter(is_active=True).exists():
                messages.error(request, '没有启用的产线,无法排产 —— 请先到基础台账启用/新增产线。')
            else:
                messages.info(request, '排产队列为空 —— 没有待排产订单。可到基础台账新增订单(状态=待排产)。')
        else:
            tip = f'✅ 一键排产完成:{result.scheduled} 张订单已铺到甘特计划。'
            if result.late:
                tip += f' ⚠ 其中 {result.late} 张预计超期,请关注。'
            else:
                tip += f' 全部 {result.on_time} 张可按期交付。'
            messages.success(request, tip)
        return redirect(reverse('board:schedule'))

    now = tz.now()
    queue = pending_orders()

    # 各产线当前负载(未完成计划的工时与条数)。
    lines = Line.objects.filter(is_active=True).order_by('code')
    load = []
    for line in lines:
        active_plans = Plan.objects.filter(line=line, end_time__gt=now).exclude(progress=100)
        agg = active_plans.aggregate(n=Count('id'))
        hours = sum(p.duration_hours for p in active_plans)
        last_end = active_plans.order_by('-end_time').values_list('end_time', flat=True).first()
        load.append({
            'line': line,
            'plan_count': agg['n'] or 0,
            'hours': round(hours, 1),
            'free_at': last_end or now,
            'capacity': line.capacity,
        })
    max_hours = max([x['hours'] for x in load], default=0) or 1

    # 甘特:展示未完成计划(在产 + 已排)。
    plans = (
        Plan.objects.select_related('order', 'line')
        .filter(end_time__gt=now).order_by('line__code', 'start_time')
    )
    lanes, axis, window = _gantt(plans, now)

    from .risk import analyze_risk
    risk = getattr(request, '_risk_report', None) or analyze_risk(now)

    ctx = {
        'queue': queue,
        'risk': risk,
        'risk_counts': risk.counts,
        'queue_count': len(queue),
        'load': load,
        'max_hours': max_hours,
        'lanes': lanes,
        'axis': axis,
        'window': window,
        'plan_count': plans.count(),
        'now': now,
    }
    return render(request, 'board/schedule.html', ctx)
