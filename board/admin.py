from django.contrib import admin

from .models import Line, Process, Order, Plan, ProductionLog


@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'foreman', 'capacity', 'is_active')
    search_fields = ('code', 'name', 'foreman')


@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'line', 'seq', 'std_hours')
    list_filter = ('line',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'product', 'quantity', 'due_date', 'priority', 'status')
    list_filter = ('status', 'priority')
    search_fields = ('order_no', 'product', 'customer')


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('order', 'line', 'process', 'start_time', 'end_time', 'progress')
    list_filter = ('line',)


@admin.register(ProductionLog)
class ProductionLogAdmin(admin.ModelAdmin):
    list_display = ('logged_at', 'line', 'process', 'order', 'shift',
                    'qty_ok', 'qty_ng', 'operator', 'source')
    list_filter = ('line', 'shift', 'source')
    search_fields = ('operator', 'remark', 'order__order_no')
    date_hierarchy = 'logged_at'
