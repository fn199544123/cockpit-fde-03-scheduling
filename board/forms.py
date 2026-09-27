"""基础台账表单。用 ModelForm 统一渲染,给每个控件挂上深色大屏样式类。"""

from django import forms

from .models import Line, Process, Order, ProductionLog


_INPUT = 'field-input'
_SELECT = 'field-input field-select'


class LineForm(forms.ModelForm):
    class Meta:
        model = Line
        fields = ['name', 'code', 'foreman', 'capacity', 'is_active', 'remark']
        widgets = {
            'name': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '如:一线'}),
            'code': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '如:LN-01'}),
            'foreman': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '师傅化名,如:老张'}),
            'capacity': forms.NumberInput(attrs={'class': _INPUT, 'min': 0}),
            'is_active': forms.CheckboxInput(attrs={'class': 'field-check'}),
            'remark': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '选填'}),
        }


class ProcessForm(forms.ModelForm):
    class Meta:
        model = Process
        fields = ['name', 'code', 'line', 'seq', 'std_hours']
        widgets = {
            'name': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '如:机加/焊接/总装'}),
            'code': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '如:OP-10'}),
            'line': forms.Select(attrs={'class': _SELECT}),
            'seq': forms.NumberInput(attrs={'class': _INPUT, 'min': 1}),
            'std_hours': forms.NumberInput(attrs={'class': _INPUT, 'step': '0.1', 'min': 0}),
        }


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = [
            'order_no', 'product', 'customer', 'quantity',
            'work_hours', 'due_date', 'priority', 'status',
        ]
        widgets = {
            'order_no': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '如:SO-20260001'}),
            'product': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '产品名称'}),
            'customer': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '脱敏客户,如:甲方A'}),
            'quantity': forms.NumberInput(attrs={'class': _INPUT, 'min': 1}),
            'work_hours': forms.NumberInput(attrs={'class': _INPUT, 'step': '0.5', 'min': 0}),
            'due_date': forms.DateInput(attrs={'class': _INPUT, 'type': 'date'}, format='%Y-%m-%d'),
            'priority': forms.Select(attrs={'class': _SELECT}),
            'status': forms.Select(attrs={'class': _SELECT}),
        }


class ProductionLogForm(forms.ModelForm):
    """报工记录表单。同时服务「快速录入」页与「台账」通用编辑页。"""

    class Meta:
        model = ProductionLog
        fields = [
            'line', 'process', 'order', 'operator', 'shift',
            'qty_ok', 'qty_ng', 'logged_at', 'remark',
        ]
        widgets = {
            'line': forms.Select(attrs={'class': _SELECT}),
            'process': forms.Select(attrs={'class': _SELECT}),
            'order': forms.Select(attrs={'class': _SELECT}),
            'operator': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '师傅化名,如:老张'}),
            'shift': forms.Select(attrs={'class': _SELECT}),
            'qty_ok': forms.NumberInput(attrs={'class': _INPUT, 'min': 0}),
            'qty_ng': forms.NumberInput(attrs={'class': _INPUT, 'min': 0}),
            'logged_at': forms.DateTimeInput(
                attrs={'class': _INPUT, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'remark': forms.TextInput(attrs={'class': _INPUT, 'placeholder': '选填'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # datetime-local 需要匹配的输入格式
        self.fields['logged_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S']
        # 工序/订单可空,给出友好占位
        self.fields['process'].empty_label = '— 不区分工序 —'
        self.fields['order'].empty_label = '— 不关联订单 —'

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get('qty_ok') or cleaned.get('qty_ng')):
            raise forms.ValidationError('合格数与不良数不能同时为 0,请录入实际产出。')
        process, line = cleaned.get('process'), cleaned.get('line')
        if process and line and process.line_id is not None and process.line_id != line.pk:
            self.add_error('process', '所选工序不属于当前产线，请重新选择。')
        return cleaned
