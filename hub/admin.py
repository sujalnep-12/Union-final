from django.contrib import admin

from .models import (
    AuditLog,
    Community,
    CommunityEvent,
    CommunityMembership,
    CommunityPost,
    ContactMessage,
    ContentReport,
    Notification,
    PaymentTransaction,
    PostComment,
    PostEngagement,
    SubscriptionPlan,
    UserProfile,
    UserSubscription,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_number", "gender", "date_of_birth", "theme_preference", "email_verified")
    list_filter = ("gender", "theme_preference", "email_verified", "mfa_enabled")
    search_fields = ("user__username", "user__email", "phone_number", "address", "interests")


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ("name", "topic", "visibility", "owner", "allow_member_posts", "is_archived", "created_at")
    list_filter = ("visibility", "allow_member_posts", "is_archived", "topic", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "description", "rules", "topic", "owner__username", "owner__email")


@admin.register(CommunityMembership)
class CommunityMembershipAdmin(admin.ModelAdmin):
    list_display = ("community", "user", "role", "joined_at")
    list_filter = ("role", "joined_at")
    search_fields = ("community__name", "user__username", "user__email")


@admin.register(CommunityPost)
class CommunityPostAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "author", "category", "content_type", "safety_status", "is_pinned", "created_at")
    search_fields = ("title", "author", "author_user__username", "community__name", "body", "ai_summary")
    list_filter = ("category", "content_type", "safety_status", "is_pinned", "community", "created_at")


@admin.register(PostComment)
class PostCommentAdmin(admin.ModelAdmin):
    list_display = ("post", "author", "created_at")
    search_fields = ("post__title", "author__username", "body")
    list_filter = ("created_at",)


@admin.register(PostEngagement)
class PostEngagementAdmin(admin.ModelAdmin):
    list_display = ("post", "user", "engagement_type", "created_at")
    list_filter = ("engagement_type", "created_at")
    search_fields = ("post__title", "user__username", "user__email")


@admin.register(ContentReport)
class ContentReportAdmin(admin.ModelAdmin):
    list_display = ("reason", "status", "ai_category", "ai_priority", "reporter", "post", "created_at")
    list_filter = ("reason", "status", "ai_category", "ai_priority", "created_at")
    search_fields = ("description", "resolution_notes", "reporter__username", "post__title", "community__name")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "community_limit", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "description", "feature_summary")


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "starts_at", "expires_at")
    list_filter = ("status", "plan", "starts_at")
    search_fields = ("user__username", "user__email", "plan__name")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "gateway", "status", "amount", "created_at")
    list_filter = ("gateway", "status", "created_at")
    search_fields = ("user__username", "user__email", "reference_code", "notes")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "category", "is_read", "created_at")
    list_filter = ("category", "is_read", "created_at")
    search_fields = ("title", "body", "user__username", "user__email")


@admin.register(CommunityEvent)
class CommunityEventAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "organizer", "starts_at")
    list_filter = ("starts_at", "community")
    search_fields = ("title", "description", "community__name", "organizer__username")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "actor", "target", "severity", "created_at")
    list_filter = ("severity", "created_at")
    search_fields = ("action", "target", "details", "actor__username")
    readonly_fields = ("created_at",)


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "created_at")
    search_fields = ("full_name", "email", "message")
    readonly_fields = ("created_at",)
