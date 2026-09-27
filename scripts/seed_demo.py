"""生成演示数据(高度脱敏)。用法:

    .venv/bin/python scripts/seed_demo.py

幂等:重复运行会先清空 board 数据再重建。所有名称/编号均为虚构或化名,
严禁真实企业 / 人名 / 地名 —— 企业统一写「某企业」,产线用一线/二线/三线,
工序用机加/焊接/总装,师傅一律化名(张师傅、李师傅……)。

数据量刻意做「饱满」:
  · 报工记录铺满近 14 天(对齐大屏的 14 天产出趋势曲线),三班全覆盖;
  · 排产计划铺满三条产线的甘特图(过去在产 → 未来已排,错峰不重叠);
  · 订单覆盖 待排/已排/在产/已完成 四态,交期做出 逾期/临期/关注/充裕 梯度;
  · 保留一张「已排产却查无计划」的异常单,给交期风险预警识别。
"""

import os
import sys
from datetime import timedelta

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')
django.setup()

from django.utils import timezone  # noqa: E402

from board.models import Line, Process, Order, Plan, ProductionLog  # noqa: E402


def run():
    ProductionLog.objects.all().delete()
    Plan.objects.all().delete()
    Order.objects.all().delete()
    Process.objects.all().delete()
    Line.objects.all().delete()

    # ————————————————— 产线(主数据)—————————————————
    lines_def = [
        ('一线', 'L01', '张师傅', 120, '某企业总装主线,节拍最稳'),
        ('二线', 'L02', '李师傅', 90, '焊接为主,承接中小批量'),
        ('三线', 'L03', '王师傅', 150, '机加高产能线,支持三班倒'),
    ]
    lines = []
    for name, code, foreman, cap, remark in lines_def:
        lines.append(Line.objects.create(
            name=name, code=code, foreman=foreman, capacity=cap, remark=remark,
        ))

    # ————————————————— 工序(每线:机加→焊接→总装)—————————————————
    proc_def = ['机加', '焊接', '总装']
    for line in lines:
        for i, pname in enumerate(proc_def, start=1):
            Process.objects.create(
                name=pname, code=f'{line.code}-P{i}', line=line, seq=i,
                std_hours=round(0.5 + i * 0.4, 2),
            )

    now = timezone.now()

    products = [
        'A型减速机', 'B型阀体', 'C型法兰', 'D型齿轮箱', 'E型支架', 'F型泵壳',
        'G型联轴器', 'H型轴承座', 'J型端盖', 'K型异形件', 'M型机座', 'N型主轴',
        'P型壳体', 'Q型连杆', 'R型托架', 'S型齿圈', 'T型缸体', 'U型叶轮',
        'V型密封座', 'W型转子', 'X型定子', 'Y型底板', 'Z型护罩', 'AA型丝杆',
    ]
    customers = ['某企业', '某企业(华东)', '某企业(西南)', '某企业(装备事业部)',
                 '某企业(农机板块)', '某企业(重工分厂)']

    # 订单剧本:(状态, 进度或None, 优先级, 交期偏移天, 总工时)
    # 交期偏移:负=已逾期,0/1=临期紧张,2~4=需关注,>=5=充裕。
    order_script = [
        # —— 已完成(沉淀历史,充实订单结构环图与产出统计)——
        ('done', 100, 2, -8, 12), ('done', 100, 3, -6, 16),
        ('done', 100, 2, -5, 9),  ('done', 100, 1, -4, 20),
        ('done', 100, 3, -3, 11), ('done', 100, 2, -2, 14),
        # —— 生产中(甘特上正在跑的条,进度不一)——
        ('producing', 85, 4, -1, 18), ('producing', 60, 3, 0, 14),
        ('producing', 45, 4, 1, 22), ('producing', 30, 2, 2, 10),
        ('producing', 70, 3, 1, 16),
        # —— 已排产(未来已铺计划,等开工)——
        ('scheduled', 15, 4, 1, 12), ('scheduled', 0, 3, 2, 20),
        ('scheduled', 0, 2, 3, 8),  ('scheduled', 0, 3, 4, 15),
        ('scheduled', 0, 4, 2, 24), ('scheduled', 0, 2, 5, 11),
        # —— 待排产(排产队列,等「一键自动排产」)——
        ('pending', None, 4, 1, 13), ('pending', None, 3, 2, 9),
        ('pending', None, 2, 3, 17), ('pending', None, 4, 4, 21),
        ('pending', None, 1, 6, 7),  ('pending', None, 3, 5, 19),
        ('pending', None, 2, 8, 10), ('pending', None, 4, 3, 15),
    ]

    orders = []
    for i, (status, progress, prio, due_off, wh) in enumerate(order_script):
        o = Order.objects.create(
            order_no=f'SO-2026{1001 + i}',
            product=products[i % len(products)],
            customer=customers[i % len(customers)],
            quantity=(i % 8 + 2) * 40,
            work_hours=wh,
            due_date=(now + timedelta(days=due_off)).date(),
            priority=prio,
            status=status,
        )
        orders.append((o, status, progress, due_off))

    # 数据异常演示:一张「已排产」订单却查无排产计划(排产口径异常),供预警识别。
    Order.objects.create(
        order_no='SO-20261099', product='K型异形件', customer='某企业(重工分厂)',
        quantity=200, work_hours=15,
        due_date=(now + timedelta(days=3)).date(),
        priority=3, status='scheduled',
    )

    # ————————————————— 排产计划:铺满三条产线的甘特图 —————————————————
    # 分状态铺:已完成→落在过去(沉淀历史);生产中→跨越此刻(正在跑);
    # 已排产→顺延到未来。每条线各自维护游标,首尾错峰不重叠,保证三条线
    # 的甘特都有「正在进行 + 未来已排」的条带,大屏一开就满。
    def _plan(o, line, start, end, progress):
        Plan.objects.create(
            order=o, line=line,
            process=line.processes.order_by('seq').first(),
            start_time=start, end_time=end, progress=progress,
        )

    done = [(o, p) for (o, s, p, d) in orders if s == 'done']
    producing = [(o, p) for (o, s, p, d) in orders if s == 'producing']
    scheduled = [(o, p) for (o, s, p, d) in orders if s == 'scheduled']

    # 历史:已完成订单铺到过去 6 天,单纯充实甘特左侧与产出统计。
    past_cursor = {line.id: now - timedelta(days=6) for line in lines}
    for i, (o, prog) in enumerate(done):
        line = lines[i % len(lines)]
        start = past_cursor[line.id] + timedelta(hours=3 + (o.id % 3) * 2)
        end = start + timedelta(hours=o.work_hours)
        past_cursor[line.id] = end
        _plan(o, line, start, end, prog)

    # 在产:让每条在产计划跨越此刻 —— 已完成部分在左,未完成部分在右。
    line_free = {line.id: now for line in lines}  # 该线未来的下一空闲时刻
    for i, (o, prog) in enumerate(producing):
        line = lines[i % len(lines)]
        elapsed = o.work_hours * (prog or 0) / 100.0
        start = now - timedelta(hours=elapsed)
        end = start + timedelta(hours=o.work_hours)
        line_free[line.id] = max(line_free[line.id], end)
        _plan(o, line, start, end, prog)

    # 已排产:顺着每条线的空闲时刻往未来铺,错峰留缓冲。
    for i, (o, prog) in enumerate(scheduled):
        line = lines[i % len(lines)]
        start = line_free[line.id] + timedelta(hours=2 + (o.id % 3) * 2)
        end = start + timedelta(hours=o.work_hours)
        line_free[line.id] = end
        _plan(o, line, start, end, prog)

    # ————————————————— 报工采集:铺满近 14 天,三班全覆盖 —————————————————
    operators = ['张师傅', '李师傅', '王师傅', '赵师傅', '孙师傅', '周师傅']
    shifts = ['day', 'mid', 'night']
    finished = [o for (o, s, p, d) in orders if s in ('producing', 'done')]
    for d in range(14):                      # 近 14 天(含今天)
        for li_, line in enumerate(lines):
            for si, shift in enumerate(shifts):
                # 产量随线/班/日波动,贴近额定日产能的一个班次量
                base = line.capacity // 3
                ok = base + (li_ * 5 + si * 7 + d * 3) % 28 - 6
                ok = max(ok, 8)
                ng = (li_ + si + d) % 5        # 0~4 件不良
                # 夜班略降、周期性小波峰,做出趋势曲线的起伏
                if shift == 'night':
                    ok = int(ok * 0.85)
                if d % 7 in (5, 6):            # 周末小低谷
                    ok = int(ok * 0.7)
                logged = now - timedelta(days=d, hours=si * 8 + 2)
                proc = list(line.processes.order_by('seq'))[si % 3]
                order = finished[(li_ * 3 + si + d) % len(finished)] if finished else None
                ProductionLog.objects.create(
                    line=line, process=proc, order=order,
                    operator=operators[(li_ + si + d) % len(operators)],
                    shift=shift, qty_ok=ok, qty_ng=ng,
                    logged_at=logged,
                    source='quick' if d < 2 else 'manual',
                )

    print(
        f'产线 {Line.objects.count()} 条, 工序 {Process.objects.count()} 个, '
        f'订单 {Order.objects.count()} 张, 排产计划 {Plan.objects.count()} 条, '
        f'报工记录 {ProductionLog.objects.count()} 条。'
    )


if __name__ == '__main__':
    run()
