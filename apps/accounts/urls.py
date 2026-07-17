from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Auth
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("password-change/", views.PasswordChangeView.as_view(), name="password_change"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),

    # Profile & invitations
    path("profile/", views.profile, name="profile"),
    path("invitations/accept/<str:token>/", views.accept_invitation, name="invitation_accept"),

    # Administration (custom admin area)
    path("manage/users/", views.UserListView.as_view(), name="manage_users"),
    path("manage/users/<int:pk>/edit/", views.user_edit, name="manage_user_edit"),
    path("manage/users/<int:pk>/set-active/", views.user_set_active, name="manage_user_set_active"),
    path("manage/invitations/", views.InvitationListView.as_view(), name="manage_invitations"),
    path("manage/invitations/new/", views.invite_user, name="manage_invite"),
    path("manage/invitations/<int:pk>/resend/", views.invitation_resend, name="manage_invitation_resend"),
    path("manage/invitations/<int:pk>/cancel/", views.invitation_cancel, name="manage_invitation_cancel"),
]
