from django.urls import path

from . import views

app_name = 'board'

urlpatterns = [
    path('', views.board, name='board'),

    # 交期风险预警(排产结果对照交期,分级预警看板)
    path('alerts/', views.alerts, name='alerts'),

    # 智能排产(队列 → 一键自动排产 → 甘特计划)
    path('schedule/', views.schedule, name='schedule'),

    # 产线负载率与产能利用分析
    path('utilization/', views.utilization, name='utilization'),

    # 数据采集(快速报工)
    path('collect/', views.collect, name='collect'),

    # 基础台账(主数据)管理
    path('ledger/', views.ledger_home, name='ledger_home'),
    path('ledger/<str:entity>/', views.ledger_list, name='ledger_list'),
    path('ledger/<str:entity>/new/', views.ledger_edit, name='ledger_new'),
    path('ledger/<str:entity>/<int:pk>/edit/', views.ledger_edit, name='ledger_edit'),
    path('ledger/<str:entity>/<int:pk>/delete/', views.ledger_delete, name='ledger_delete'),
]
