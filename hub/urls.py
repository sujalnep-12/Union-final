from django.urls import path

from . import views

app_name = "hub"

urlpatterns = [
    path("", views.home, name="home"),
    path("get-started/", views.get_started, name="get_started"),
    path("signup/", views.signup, name="signup"),
    path("verify-otp/", views.verify_otp, name="verify_otp"),
    path("google-signin/", views.google_signin, name="google_signin"),
    path("account/continue/", views.account_continue, name="account_continue"),
    path("account/switch/<int:user_id>/", views.switch_account, name="switch_account"),
    path("reset-password/", views.reset_password, name="reset_password"),
    path("signin/", views.signin, name="signin"),
    path("forgot-password/", views.forgot_password, name="forgot_password"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/communities/create/", views.create_community, name="create_community"),
    path("dashboard/communities/<slug:slug>/", views.community_detail, name="community_detail"),
    path("payments/esewa/community/<int:community_id>/start/", views.esewa_community_payment_start, name="esewa_community_payment_start"),
    path("payments/esewa/subscription/<int:plan_id>/start/", views.esewa_subscription_payment_start, name="esewa_subscription_payment_start"),
    path("payments/esewa/success/", views.esewa_payment_success, name="esewa_payment_success"),
    path("payments/esewa/failure/", views.esewa_payment_failure, name="esewa_payment_failure"),
    path("dashboard/communities/<slug:slug>/rooms/add/", views.add_room, name="add_room"),
    path("dashboard/communities/<slug:slug>/rooms/<int:pk>/edit/", views.edit_room, name="edit_room"),
    path("dashboard/communities/<slug:slug>/rooms/<int:pk>/delete/", views.delete_room, name="delete_room"),
    path("dashboard/communities/<slug:slug>/rooms/reorder/", views.reorder_rooms, name="reorder_rooms"),
    path("dashboard/communities/<slug:slug>/links/add/", views.add_link, name="add_link"),
    path("dashboard/communities/<slug:slug>/links/<int:pk>/edit/", views.edit_link, name="edit_link"),
    path("dashboard/communities/<slug:slug>/links/<int:pk>/delete/", views.delete_link, name="delete_link"),
    path("dashboard/communities/<slug:slug>/links/reorder/", views.reorder_links, name="reorder_links"),
    path("dashboard/profile/<str:username>/", views.user_profile, name="user_profile"),
    path("dashboard/search/", views.dashboard_search, name="dashboard_search"),
    path("dashboard/<str:section>/", views.dashboard, name="dashboard_section"),
    path("api/messages/thread/<int:thread_id>/", views.get_message_thread, name="get_message_thread"),
    path("toggle-follow/<str:username>/", views.toggle_follow, name="toggle_follow"),
    path("logout/", views.signout, name="logout"),
    path("api/notifications/action/", views.notification_action, name="notification_action"),
    path("api/friends/action/", views.friend_request, name="friend_request"),
    path("api/dm/chat/<int:user_id>/", views.dm_chat, name="dm_chat"),
]
