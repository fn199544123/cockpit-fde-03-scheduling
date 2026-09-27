from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from .models import Line, Process, Order, Plan, ProductionLog
from .scheduler import auto_schedule
from .risk import analyze_risk
from .analytics import analyze_utilization

class SchedulingAcceptance(TestCase):
    def setUp(self):
        self.line=Line.objects.create(name='一线',code='AC-L1',capacity=100)
        self.other=Line.objects.create(name='二线',code='AC-L2',capacity=100)
        self.process=Process.objects.create(name='某工序',code='AC-P1',line=self.line)
        for i in range(3):
            Order.objects.create(order_no=f'AC-ORDER-{i}',product='某产品',customer='某企业',quantity=100,work_hours=8,due_date=timezone.localdate()+timedelta(days=i-1),priority=4-i)
    def test_schedule_no_overlap_and_idempotence(self):
        self.assertEqual(self.client.post('/schedule/').status_code,302)
        self.assertEqual(Order.objects.filter(status='pending').count(),0)
        self.assertEqual(Plan.objects.count(),3)
        for line in (self.line,self.other):
            plans=list(line.plan_set.all()) if hasattr(line,'plan_set') else list(Plan.objects.filter(line=line).order_by('start_time'))
            for a,b in zip(plans,plans[1:]):self.assertLessEqual(a.end_time,b.start_time)
        self.assertEqual(auto_schedule().scheduled,0)
        self.assertEqual(Plan.objects.count(),3)
        self.assertGreater(analyze_risk().counts['danger'],0)
        self.assertGreater(analyze_utilization(days=7).overall_load,0)
        for path in ['/','/schedule/','/alerts/','/utilization/','/ledger/','/collect/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
    def test_collect_validation_and_persistence(self):
        data=dict(line=self.line.pk,process=self.process.pk,order='',operator='化名甲',shift='day',qty_ok=10,qty_ng=2,logged_at='2026-09-27T10:00',remark='验收虚构记录')
        self.assertEqual(self.client.post('/collect/',data).status_code,302)
        self.assertEqual(ProductionLog.objects.get().qty_ok,10)
        for changes in [dict(qty_ok=0,qty_ng=0),dict(qty_ok=-1),dict(line=self.other.pk)]:
            self.assertEqual(self.client.post('/collect/',dict(data,**changes)).status_code,200)
            self.assertEqual(ProductionLog.objects.count(),1)
        self.assertContains(self.client.get('/ledger/report/'),'化名甲')
