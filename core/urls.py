from django.urls import path

from . import planner_api
from . import views
from . import work_schedule_api

app_name = "core"


urlpatterns = [
    path('', views.index, name='index'),

    path('accounts/', views.accounts, name='accounts'),

    path(
        'accounts/<uuid:pk>/',
        views.account_detail,
        name='account_detail',
    ),

    path(
        'accounts/<uuid:pk>/status/',
        views.account_status,
        name='account_status',
    ),

    path(
        'accounts/<uuid:pk>/delete/',
        views.account_delete,
        name='account_delete',
    ),

    path('transactions/', views.transactions, name='transactions'),

    path(
        'transactions/<uuid:pk>/',
        views.transaction_detail,
        name='transaction_detail',
    ),

    path(
        'transactions/<uuid:pk>/delete/',
        views.transaction_delete,
        name='transaction_delete',
    ),

    path(
        'transactions/<uuid:pk>/allocation/',
        views.transaction_allocation,
        name='transaction_allocation',
    ),

    path('budget-api/', views.budget_api, name='budget_api'),

    path(
        'budget-api/categories/',
        views.budget_categories,
        name='budget_categories',
    ),

    path(
        'budget-api/categories/<uuid:pk>/',
        views.budget_category_detail,
        name='budget_category_detail',
    ),

    path(
        'budget-api/categories/<uuid:pk>/delete/',
        views.budget_category_delete,
        name='budget_category_delete',
    ),

    path(
        'budget-api/allocations/',
        views.budget_allocations,
        name='budget_allocations',
    ),

    path(
        'budget-api/allocations/<uuid:pk>/',
        views.budget_allocation_detail,
        name='budget_allocation_detail',
    ),

    path(
        'budget-api/allocations/<uuid:pk>/status/',
        views.budget_allocation_status,
        name='budget_allocation_status',
    ),

    path(
        'budget-api/allocations/<uuid:pk>/delete/',
        views.budget_allocation_delete,
        name='budget_allocation_delete',
    ),

    path('goals-api/', views.goals_api, name='goals_api'),

    path(
        'goals-api/goals/',
        views.goal_create,
        name='goal_create',
    ),

    path(
        'goals-api/goals/<uuid:pk>/',
        views.goal_detail,
        name='goal_detail',
    ),

    path(
        'goals-api/goals/<uuid:pk>/delete/',
        views.goal_delete,
        name='goal_delete',
    ),

    path(
        'goals-api/goals/<uuid:pk>/assignments/',
        views.goal_assignments,
        name='goal_assignments',
    ),

    path(
        'goals-api/goals/<uuid:pk>/assignments/<uuid:assignment_pk>/reverse/',
        views.goal_assignment_reverse,
        name='goal_assignment_reverse',
    ),

    path('savings-api/', views.savings_api, name='savings_api'),

    path(
        'savings-api/withdrawals/',
        views.savings_withdrawals,
        name='savings_withdrawals',
    ),

    path(
        'savings-api/withdrawals/<uuid:pk>/',
        views.savings_withdrawal_detail,
        name='savings_withdrawal_detail',
    ),

    path(
        'savings-api/withdrawals/<uuid:pk>/delete/',
        views.savings_withdrawal_delete,
        name='savings_withdrawal_delete',
    ),

    path('400/', views.bad_request, name='bad_request'),

    path('403/', views.permission_denied, name='permission_denied'),

    path('404/', views.page_not_found, name='page_not_found'),

    path('500/', views.server_error, name='server_error'),

    path('budgets/', views.budgets, name='budgets'),

    path('documentation/', views.documentation, name='documentation'),

    path('goals/', views.goals, name='goals'),

    path('google/connect/', views.google_connect, name='google_connect'),

    path(
        'google/disconnect/',
        views.google_disconnect,
        name='google_disconnect',
    ),

    path('insights/', views.insights, name='insights'),

    path('insights-api/', views.insights_api, name='insights_api'),

    path('insights/pdf/', views.insights_pdf, name='insights_pdf'),

    path('login/', views.login, name='login'),

    path('logout/', views.logout, name='logout'),

    path(
        'password-reset-complete/',
        views.GabswebPasswordResetCompleteView.as_view(),
        name='password_reset_complete',
    ),

    path(
        'password-reset-confirm/<uidb64>/<token>/',
        views.GabswebPasswordResetConfirmView.as_view(),
        name='password_reset_confirm_token',
    ),

    path('password-reset-confirm/', views.password_reset_confirm, name='password_reset_confirm'),

    path(
        'password-reset-done/',
        views.GabswebPasswordResetDoneView.as_view(),
        name='password_reset_done',
    ),

    path(
        'password-reset/',
        views.GabswebPasswordResetView.as_view(),
        name='password_reset',
    ),

    path('password/set/', views.set_password, name='set_password'),

    path('password/change/', views.change_password, name='change_password'),

    path('planner/', views.planner, name='planner'),

    path('planner-api/', planner_api.planner_api, name='planner_api'),

    path(
        'planner-api/migrate/',
        planner_api.planner_migrate,
        name='planner_migrate',
    ),

    path(
        'planner-api/daily-plans/',
        planner_api.daily_plan_create,
        name='daily_plan_create',
    ),

    path(
        'planner-api/daily-plans/ensure/',
        planner_api.daily_plan_ensure,
        name='daily_plan_ensure',
    ),

    path(
        'planner-api/daily-plans/by-date/<str:day>/',
        planner_api.daily_plan_by_date,
        name='daily_plan_by_date',
    ),

    path(
        'planner-api/daily-plans/<uuid:pk>/',
        planner_api.daily_plan_detail,
        name='daily_plan_detail',
    ),

    path(
        'planner-api/daily-plans/<uuid:pk>/tasks/',
        planner_api.daily_task_create,
        name='daily_task_create',
    ),

    path(
        'planner-api/daily-plans/<uuid:pk>/tasks/<uuid:task_pk>/',
        planner_api.daily_task_detail,
        name='daily_task_detail',
    ),

    path(
        'planner-api/daily-plans/<uuid:pk>/top-three/',
        planner_api.daily_top_three,
        name='daily_top_three',
    ),

    path(
        'planner-api/daily-plans/<uuid:pk>/meetings/',
        planner_api.daily_meeting_create,
        name='daily_meeting_create',
    ),

    path(
        'planner-api/daily-plans/<uuid:pk>/meetings/<uuid:meeting_pk>/',
        planner_api.daily_meeting_detail,
        name='daily_meeting_detail',
    ),

    path(
        'planner-api/projects/',
        planner_api.project_create,
        name='project_create',
    ),

    path(
        'planner-api/projects/<uuid:pk>/',
        planner_api.project_detail,
        name='project_detail',
    ),

    path(
        'planner-api/projects/<uuid:pk>/milestones/',
        planner_api.milestone_create,
        name='milestone_create',
    ),

    path(
        'planner-api/projects/<uuid:pk>/milestones/<uuid:milestone_pk>/',
        planner_api.milestone_detail,
        name='milestone_detail',
    ),

    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),

    path('register/', views.register, name='register'),

    path('savings/', views.savings, name='savings'),

    path('settings/', views.settings, name='settings'),

    path('terms/', views.terms, name='terms'),

    path('transaction/', views.transaction, name='transaction'),

    path('profile/image/', views.profile_image, name='profile_image'),

    path('profile-api/', views.profile_api, name='profile_api'),

    path('profile/update/', views.update_profile, name='update_profile'),

    path(
        'work-profile-api/',
        views.work_profile_api,
        name='work_profile_api',
    ),

    path(
        'supervisors-api/',
        views.supervisors_api,
        name='supervisors_api',
    ),

    path(
        'supervisors-api/<uuid:pk>/',
        views.supervisor_detail,
        name='supervisor_detail',
    ),

    path(
        'personal-shifts-api/',
        views.personal_shifts_api,
        name='personal_shifts_api',
    ),

    path(
        'personal-shifts-api/<uuid:pk>/',
        views.personal_shift_detail,
        name='personal_shift_detail',
    ),

    path(
        'notifications-api/',
        views.notifications_api,
        name='notifications_api',
    ),

    path(
        'notifications-api/<uuid:pk>/read/',
        views.notification_read,
        name='notification_read',
    ),

    path(
        'notifications-api/read-all/',
        views.notifications_read_all,
        name='notifications_read_all',
    ),

    path(
        'notifications-api/clear/',
        views.notifications_clear,
        name='notifications_clear',
    ),

    path('work-schedule/', views.work_schedule, name='work_schedule'),

    path(
        'work-schedule-api/',
        work_schedule_api.schedule_api,
        name='work_schedule_api',
    ),
    path(
        'work-schedule-api/records/',
        work_schedule_api.schedule_record_create,
        name='work_schedule_record_create',
    ),
    path(
        'work-schedule-api/records/<uuid:pk>/',
        work_schedule_api.schedule_record_detail,
        name='work_schedule_record_detail',
    ),
    path(
        'work-schedule-api/config/',
        work_schedule_api.schedule_config_api,
        name='work_schedule_config_api',
    ),
    path(
        'work-schedule-api/preview/',
        work_schedule_api.schedule_preview_api,
        name='work_schedule_preview_api',
    ),
    path(
        'work-schedule-api/confirm/',
        work_schedule_api.schedule_confirm_api,
        name='work_schedule_confirm_api',
    ),
    path(
        'work-schedule-api/migrate/',
        work_schedule_api.schedule_migrate_api,
        name='work_schedule_migrate_api',
    ),
]
