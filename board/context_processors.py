"""模板上下文处理器 —— 让「交期预警」计数在每个页面的导航栏都能亮起徽标。

轻量:只算一次预警报告并缓存到 request 上,避免同一请求内重复计算。
"""

from .risk import analyze_risk


def risk_badge(request):
    """向所有模板注入 `risk_badge`:{danger, warning, total, level}。"""
    report = getattr(request, '_risk_report', None)
    if report is None:
        report = analyze_risk()
        request._risk_report = report
    c = report.counts
    return {'risk_badge': {
        'danger': c['danger'],
        'warning': c['warning'],
        'abnormal': c['abnormal'],
        'total': c['total'],
        'level': report.banner_level,
    }}
