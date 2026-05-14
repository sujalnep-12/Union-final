from django.urls import path

from . import views

app_name = "hub"

urlpatterns = [
    path("", views.home, name="home"),
    path("get-started/", views.get_started, name="get_started"),
    path("signup/", views.signup, name="signup"),
    path("verify-otp/", views.verify_otp, name="verify_otp"),
    path("signin/", views.signin, name="signin"),
    path("forgot-password/", views.forgot_password, name="forgot_password"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/<str:section>/", views.dashboard, name="dashboard_section"),
    path("logout/", views.signout, name="logout"),
]
