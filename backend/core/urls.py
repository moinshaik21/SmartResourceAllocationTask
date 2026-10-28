from django.urls import path
from . import views as v

urlpatterns = [
    path("auth/login/", v.LoginView.as_view()),
    path("auth/me/", v.MeView.as_view()),

    path("dashboard/", v.DashboardView.as_view()),
    path("captures/", v.CaptureListView.as_view()),
    path("captures/<int:pk>/assign/", v.CaptureAssignView.as_view()),
    path("projects/", v.ProjectListView.as_view()),
    path("sheet/sync/", v.SyncView.as_view()),
    path("cron/sync/", v.CronSyncView.as_view()),
    path("distribute/", v.DistributeView.as_view()),
    path("resources/", v.ResourceListView.as_view()),
    path("resources/<int:pk>/", v.ResourceDetailView.as_view()),
    path("roster/", v.RosterView.as_view()),
    path("reset-day/", v.ResetDayView.as_view()),

    path("me/work/", v.MyWorkView.as_view()),
    path("me/request-task/", v.RequestTaskView.as_view()),
    path("captures/<int:pk>/pick/", v.PickView.as_view()),
    path("captures/<int:pk>/splits/", v.DeclareSplitsView.as_view()),
    path("splits/<int:pk>/<str:action>/", v.SplitActionView.as_view()),

    path("notifications/", v.NotificationsView.as_view()),

    path("sheet/config/", v.SheetConfigView.as_view()),
    path("shift/start/", v.StartShiftView.as_view()),
    path("roster/fetch/", v.RosterFetchView.as_view()),
    path("transfers/", v.TransferListView.as_view()),
    path("transfers/<int:pk>/resolve/", v.TransferResolveView.as_view()),
    path("captures/<int:pk>/transfer/", v.CaptureTransferView.as_view()),
    path("captures/<int:pk>/wrapup/", v.WrapupView.as_view()),
    path("pipelines/", v.PipelinesView.as_view()),
    path("ms/status/", v.MsStatusView.as_view()),
    path("ms/connect/", v.MsConnectView.as_view()),
    path("ms/disconnect/", v.MsDisconnectView.as_view()),
]
