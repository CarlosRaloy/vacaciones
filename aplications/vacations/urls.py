from django.urls import path
from . import views

app_name = 'vacations'

urlpatterns = [
    path('', views.calendar_view, name='calendar'),
    path('events.json', views.events_json, name='events_json'),
    path('employee-info/<int:user_id>/', views.employee_info, name='employee_info'),
    path('request/create/', views.create_request, name='create'),
    path('leader/inbox/', views.leader_inbox, name='leader_inbox'),
    path('hr/inbox/', views.hr_inbox, name='hr_inbox'),
    path('<int:pk>/leader-act/', views.leader_act, name='leader_act'),
    path('<int:pk>/hr-act/', views.hr_act, name='hr_act'),
    path('<int:pk>/cancel/', views.cancel_request, name='cancel'),
]
