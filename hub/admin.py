from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Count, Q, Sum, Avg, F
from django.http import HttpResponse
from datetime import datetime, timedelta
from django.contrib import messages
from django.contrib.admin import display
import csv
from django.utils import timezone
from django.utils.text import Truncator

from .models import (
    AuditLog,
    Community,
    CommunityEvent,
    CommunityMembership,
    CommunityPost,
    ContactMessage,
    ContentReport,
    DirectMessage,
    DirectMessageThread,
    Notification,
    PaymentTransaction,
    PayoutRequest,
    PostComment,
    PostEngagement,
    SubscriptionPlan,
    SubscriptionTierLimits,
    SubscriptionRole,
    CommunitySubscriptionAccess,
    SubscriptionActivityLog,
    SubscriptionFeatureAccess,
    UserProfile,
    UserSubscription,
)
@admin.display(description="Full Name")
def get_user_full_name(obj):
    return f"{obj.first_name} {obj.last_name}" if obj.first_name else obj.username


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "User Profile"
    fields = (
        "platform_role", "phone_number", "gender", "date_of_birth", "theme_preference", 
        "mfa_enabled", "email_verified", "kyc_verified", "profile_image"
    )
    readonly_fields = ("email_verified",)
    extra = 0


class CustomUserAdmin(DefaultUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ("username", "email", get_user_full_name, "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("username", "email", "first_name", "last_name")
    readonly_fields = ("date_joined", "last_login")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            qs = qs.filter(is_superuser=False)
        return qs

    def save_model(self, request, obj, form, change):
        if not change:
            obj.set_password(form.cleaned_data["password"])
        super().save_model(request, obj, form, change)

    def get_inline_instances(self, request, obj=None, **kwargs):
        inlines = super().get_inline_instances(request, obj, **kwargs)
        return [inline for inline in inlines if isinstance(inline, UserProfileInline)]


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


class EnhancedCommunityMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "community_display", "user_display", "role_display", "joined_at",
        "is_owner", "is_moderator"
    )
    list_filter = ("role", "joined_at", "community")
    search_fields = (
        "community__name", "user__username", "user__email",
        "community__topic", "community__location"
    )
    readonly_fields = (
        "joined_at", "community_display", 
        "user_display", "role_display", "is_owner", "is_moderator",
        "community_summary"
    )
    actions = ["export_as_csv", "make_owner", "make_moderator", "make_member", "bulk_delete"]

    @admin.display(description="Community")
    def community_display(self, obj):
        return format_html(
            '<a href="/admin/hub/community/{}/change/">{}</a>',
            obj.community.id,
            obj.community.name
        )

    @admin.display(description="User")
    def user_display(self, obj):
        return format_html(
            '<a href="/admin/auth/user/{}/change/">{}</a>',
            obj.user.id,
            obj.user.username
        )

    @admin.display(description="Role")
    def role_display(self, obj):
        colors = {
            "owner": "red",
            "moderator": "blue",
            "member": "green"
        }
        color = colors.get(obj.role, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_role_display()
        )

    @admin.display(description="Is Owner")
    def is_owner(self, obj):
        return obj.role == CommunityMembership.OWNER

    @admin.display(description="Is Moderator")
    def is_moderator(self, obj):
        return obj.role in [CommunityMembership.OWNER, CommunityMembership.MODERATOR]

    @admin.display(description="Community Summary")
    def community_summary(self, obj):
        return format_html(
            '<div style="background: #f5f5f5; padding: 10px; border-radius: 5px;">'
            '<strong>Community Stats:</strong><br>'
            '<strong>Total Members:</strong> {}<br>'
            '<strong>Active Posts:</strong> {}<br>'
            '<strong>Events:</strong> {}'
            '</div>',
            obj.community.memberships.count(),
            obj.community.posts.count(),
            obj.community.events.count()
        )

    @admin.action(description="Export to CSV")
    def export_as_csv(self, request, queryset):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename=community_memberships.csv'
        writer = csv.writer(response)

        writer.writerow([
            'Community', 'User', 'Role', 'Joined At', 'Is Owner', 'Is Moderator'
        ])

        for membership in queryset:
            writer.writerow([
                membership.community.name,
                membership.user.username,
                membership.get_role_display(),
                membership.joined_at,
                membership.role == CommunityMembership.OWNER,
                membership.role in [CommunityMembership.OWNER, CommunityMembership.MODERATOR]
            ])

        return response

    @admin.action(description="Make Selected Owners")
    def make_owner(self, request, queryset):
        updated = 0
        for membership in queryset:
            membership.role = CommunityMembership.OWNER
            membership.save()
            updated += 1

        self.message_user(
            request,
            f"{updated} membership(s) made owner.",
            messages.SUCCESS
        )

    @admin.action(description="Make Selected Moderators")
    def make_moderator(self, request, queryset):
        updated = 0
        for membership in queryset:
            membership.role = CommunityMembership.MODERATOR
            membership.save()
            updated += 1

        self.message_user(
            request,
            f"{updated} membership(s) made moderator.",
            messages.SUCCESS
        )

    @admin.action(description="Make Selected Members")
    def make_member(self, request, queryset):
        updated = 0
        for membership in queryset:
            membership.role = CommunityMembership.MEMBER
            membership.save()
            updated += 1

        self.message_user(
            request,
            f"{updated} membership(s) made member.",
            messages.SUCCESS
        )

    @admin.action(description="Bulk Delete Memberships")
    def bulk_delete(self, request, queryset):
        deleted_count, _ = queryset.delete()
        self.message_user(
            request,
            f"{deleted_count} membership(es) deleted.",
            messages.WARNING
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions = {
                k: v for k, v in actions.items()
                if k in ['export_as_csv']
            }
        return actions


class EnhancedPostEngagementAdmin(admin.ModelAdmin):
    list_display = (
        "post_display", "user_display", "engagement_type_display",
        "note_preview", "created_at"
    )
    list_filter = ("engagement_type", "created_at", "post")
    search_fields = (
        "post__title", "user__username", "user__email", "note"
    )
    readonly_fields = (
        "created_at", "post_display", "user_display",
        "engagement_type_display", "engagement_summary"
    )
    actions = ["export_as_csv", "delete_engagements"]

    @admin.display(description="Post")
    def post_display(self, obj):
        return format_html(
            '<a href="/admin/hub/communitypost/{}/change/">{}</a>',
            obj.post.id,
            obj.post.title[:50]
        )

    @admin.display(description="User")
    def user_display(self, obj):
        return format_html(
            '<a href="/admin/auth/user/{}/change/">{}</a>',
            obj.user.id,
            obj.user.username
        )

    @admin.display(description="Type")
    def engagement_type_display(self, obj):
        return format_html(
            '<span style="background: #e3f2fd; padding: 3px 8px; border-radius: 12px; font-size: 12px;">{}</span>',
            obj.get_engagement_type_display()
        )

    @admin.display(description="Note Preview")
    def note_preview(self, obj):
        if obj.note:
            return obj.note[:50] + "..." if len(obj.note) > 50 else obj.note
        return "-"

    @admin.display(description="Engagement Summary")
    def engagement_summary(self, obj):
        return format_html(
            '<div style="background: #f8f9fa; padding: 10px; border-radius: 5px;">'
            '<strong>Engagement Type:</strong> {}<br>'
            '<strong>Note:</strong> {}<br>'
            '<strong>Created:</strong> {}'
            '</div>',
            obj.get_engagement_type_display(),
            obj.note or "No note",
            obj.created_at
        )

    @admin.action(description="Export to CSV")
    def export_as_csv(self, request, queryset):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename=post_engagements.csv'
        writer = csv.writer(response)

        writer.writerow([
            'Post', 'User', 'Engagement Type', 'Note', 'Created At'
        ])

        for engagement in queryset:
            writer.writerow([
                engagement.post.title,
                engagement.user.username,
                engagement.get_engagement_type_display(),
                engagement.note or '',
                engagement.created_at,
            ])

        return response

    @admin.action(description="Delete Selected Engagements")
    def delete_engagements(self, request, queryset):
        deleted_count, _ = queryset.delete()
        self.message_user(
            request,
            f"{deleted_count} engagement(s) deleted.",
            messages.WARNING
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions = {
                k: v for k, v in actions.items()
                if k in ['export_as_csv']
            }
        return actions


admin.site.register(CommunityMembership, EnhancedCommunityMembershipAdmin)
admin.site.register(PostEngagement, EnhancedPostEngagementAdmin)


@admin.register(PayoutRequest)
class PayoutRequestAdmin(admin.ModelAdmin):
    list_display = (
        "community",
        "requested_by",
        "amount",
        "payout_method",
        "status",
        "requested_at",
        "processed_at",
    )
    list_filter = ("status", "payout_method", "requested_at", "community")
    search_fields = (
        "community__name",
        "requested_by__username",
        "requested_by__email",
        "reference_code",
    )
    readonly_fields = ("requested_at", "processed_at")
    list_select_related = ("community", "requested_by", "processed_by")

    def save_model(self, request, obj, form, change):
        if obj.status in {PayoutRequest.PAID, PayoutRequest.REJECTED, PayoutRequest.CANCELLED}:
            obj.processed_by = obj.processed_by or request.user
        super().save_model(request, obj, form, change)
