"""生产排产核心数据模型。

演示系统 —— 所有数据高度脱敏:产线用「一线/二线/三线」、工序用「机加/焊接/总装」、
师傅用化名、订单编号为虚构编号。严禁真实企业/人名/地名。
"""

from django.db import models
from django.utils import timezone


class Line(models.Model):
    """产线。工厂里的一条物理生产线。"""

    name = models.CharField('产线名称', max_length=50, unique=True)
    code = models.CharField('产线编号', max_length=20, unique=True)
    foreman = models.CharField('线长(师傅化名)', max_length=30, blank=True)
    capacity = models.PositiveIntegerField('日产能(件)', default=100)
    is_active = models.BooleanField('是否启用', default=True)
    remark = models.CharField('备注', max_length=200, blank=True)

    class Meta:
        verbose_name = '产线'
        verbose_name_plural = '产线'
        ordering = ['code']

    def __str__(self):
        return f'{self.name}({self.code})'


class Process(models.Model):
    """工序。如机加、焊接、总装等,可归属到某条产线。"""

    name = models.CharField('工序名称', max_length=50)
    code = models.CharField('工序编号', max_length=20, unique=True)
    line = models.ForeignKey(
        Line, on_delete=models.CASCADE, related_name='processes',
        verbose_name='所属产线', null=True, blank=True,
    )
    seq = models.PositiveIntegerField('工序顺序', default=1)
    std_hours = models.FloatField('单件标准工时(小时)', default=1.0)

    class Meta:
        verbose_name = '工序'
        verbose_name_plural = '工序'
        ordering = ['line__code', 'seq']

    def __str__(self):
        return self.name


class Order(models.Model):
    """订单。含交期、数量、总工时估算。"""

    PRIORITY_CHOICES = [
        (1, '低'),
        (2, '普通'),
        (3, '高'),
        (4, '紧急'),
    ]
    STATUS_CHOICES = [
        ('pending', '待排产'),
        ('scheduled', '已排产'),
        ('producing', '生产中'),
        ('done', '已完成'),
    ]

    order_no = models.CharField('订单编号', max_length=30, unique=True)
    product = models.CharField('产品名称', max_length=80)
    customer = models.CharField('客户(脱敏)', max_length=50, blank=True)
    quantity = models.PositiveIntegerField('数量(件)', default=1)
    work_hours = models.FloatField('总工时估算(小时)', default=0)
    due_date = models.DateField('交期')
    priority = models.PositiveSmallIntegerField('优先级', choices=PRIORITY_CHOICES, default=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField('创建时间', default=timezone.now)

    class Meta:
        verbose_name = '订单'
        verbose_name_plural = '订单'
        ordering = ['-priority', 'due_date']

    def __str__(self):
        return f'{self.order_no} {self.product}'


class Plan(models.Model):
    """排产计划。把某订单安排到某产线的某个时间段(可细化到工序)。"""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='plans', verbose_name='订单',
    )
    line = models.ForeignKey(
        Line, on_delete=models.CASCADE, related_name='plans', verbose_name='产线',
    )
    process = models.ForeignKey(
        Process, on_delete=models.SET_NULL, related_name='plans',
        verbose_name='工序', null=True, blank=True,
    )
    start_time = models.DateTimeField('计划开始')
    end_time = models.DateTimeField('计划结束')
    progress = models.PositiveSmallIntegerField('进度(%)', default=0)

    class Meta:
        verbose_name = '排产计划'
        verbose_name_plural = '排产计划'
        ordering = ['line__code', 'start_time']

    def __str__(self):
        return f'{self.order.order_no} @ {self.line.name}'

    @property
    def duration_hours(self):
        return (self.end_time - self.start_time).total_seconds() / 3600.0


class ProductionLog(models.Model):
    """报工记录 —— 车间一线的「数据采集」结果:某产线/工序在某班次实际产出多少。

    这是本系统的业务数据录入落库核心:主数据(产线/工序/订单)之上,持续采集
    真实生产事件。支持大屏快速录入,一条一条打卡式上报。
    """

    SHIFT_CHOICES = [
        ('day', '早班'),
        ('mid', '中班'),
        ('night', '晚班'),
    ]
    SOURCE_CHOICES = [
        ('quick', '快速录入'),
        ('manual', '台账录入'),
        ('import', '批量导入'),
    ]

    line = models.ForeignKey(
        Line, on_delete=models.CASCADE, related_name='logs', verbose_name='产线',
    )
    process = models.ForeignKey(
        Process, on_delete=models.SET_NULL, related_name='logs',
        verbose_name='工序', null=True, blank=True,
    )
    order = models.ForeignKey(
        Order, on_delete=models.SET_NULL, related_name='logs',
        verbose_name='订单', null=True, blank=True,
    )
    operator = models.CharField('报工人(师傅化名)', max_length=30, blank=True)
    shift = models.CharField('班次', max_length=10, choices=SHIFT_CHOICES, default='day')
    qty_ok = models.PositiveIntegerField('合格数(件)', default=0)
    qty_ng = models.PositiveIntegerField('不良数(件)', default=0)
    logged_at = models.DateTimeField('采集时间', default=timezone.now)
    source = models.CharField('录入方式', max_length=10, choices=SOURCE_CHOICES, default='quick')
    remark = models.CharField('备注', max_length=200, blank=True)

    class Meta:
        verbose_name = '报工记录'
        verbose_name_plural = '报工记录'
        ordering = ['-logged_at', '-id']

    def __str__(self):
        return f'{self.line.name} {self.get_shift_display()} +{self.qty_ok}'

    @property
    def qty_total(self):
        return self.qty_ok + self.qty_ng

    @property
    def yield_rate(self):
        """良品率(%)。无产出时返回 None。"""
        total = self.qty_total
        return round(self.qty_ok * 100.0 / total, 1) if total else None
