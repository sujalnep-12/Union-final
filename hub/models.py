from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class UserProfile(models.Model):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"
    THEME_SYSTEM = "system"
    THEME_LIGHT = "light"
    THEME_DARK = "dark"

    GENDER_CHOICES = [
        (MALE, "Male"),
        (FEMALE, "Female"),
        (OTHER, "Other"),
        (PREFER_NOT_TO_SAY, "Prefer not to say"),
    ]
    THEME_CHOICES = [
        (THEME_SYSTEM, "System"),
        (THEME_LIGHT, "Light"),
        (THEME_DARK, "Dark"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=30, choices=GENDER_CHOICES, default=PREFER_NOT_TO_SAY)
    phone_number = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True)
    interests = models.CharField(max_length=255, blank=True)
    profile_image = models.FileField(upload_to="profile_images/", blank=True)
    avatar_url = models.URLField(blank=True)
    theme_preference = models.CharField(max_length=20, choices=THEME_CHOICES, default=THEME_SYSTEM)
    mfa_enabled = models.BooleanField(default=True)
    account_deletion_requested_at = models.DateTimeField(null=True, blank=True)
    email_verified = models.BooleanField(default=False)
    otp_code = models.CharField(max_length=6, blank=True)
    otp_created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user} profile"

    @property
    def profile_image_url(self):
        if self.profile_image:
            return self.profile_image.url
        return self.avatar_url

    def set_otp(self, code):
        self.otp_code = code
        self.otp_created_at = timezone.now()


class Community(models.Model):
    PUBLIC = "public"
    PRIVATE = "private"

    VISIBILITY_CHOICES = [
        (PUBLIC, "Public"),
        (PRIVATE, "Private"),
    ]

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField()
    rules = models.TextField(blank=True)
    topic = models.CharField(max_length=80, default="General")
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default=PUBLIC)
    allow_member_posts = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_communities",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "communities"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "community"
            candidate = base_slug
            suffix = 2
            while Community.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CommunityMembership(models.Model):
    OWNER = "owner"
    MODERATOR = "moderator"
    MEMBER = "member"

    ROLE_CHOICES = [
        (OWNER, "Owner"),
        (MODERATOR, "Moderator"),
        (MEMBER, "Member"),
    ]

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["community__name", "user__username"]
        constraints = [
            models.UniqueConstraint(fields=["community", "user"], name="unique_community_membership"),
        ]

    def __str__(self):
        return f"{self.user} in {self.community} ({self.role})"


class CommunityPost(models.Model):
    UPDATE = "update"
    ARTICLE = "article"
    VIDEO = "video"
    EVENT = "event"

    CLEAN = "clean"
    FLAGGED = "flagged"
    REMOVED = "removed"

    CONTENT_TYPE_CHOICES = [
        (UPDATE, "Update"),
        (ARTICLE, "Article"),
        (VIDEO, "Video"),
        (EVENT, "Event"),
    ]
    SAFETY_STATUS_CHOICES = [
        (CLEAN, "Clean"),
        (FLAGGED, "Flagged"),
        (REMOVED, "Removed"),
    ]

    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="posts",
        null=True,
        blank=True,
    )
    author_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="community_posts",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=160)
    author = models.CharField(max_length=80)
    category = models.CharField(max_length=60, default="General")
    content_type = models.CharField(max_length=20, choices=CONTENT_TYPE_CHOICES, default=UPDATE)
    body = models.TextField()
    attachment_url = models.URLField(blank=True)
    ai_summary = models.TextField(blank=True)
    safety_status = models.CharField(max_length=20, choices=SAFETY_STATUS_CHOICES, default=CLEAN)
    is_pinned = models.BooleanField(default=False)
    moderation_notes = models.TextField(blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def display_author(self):
        if self.author_user:
            return self.author_user.first_name or self.author_user.get_username().split("@")[0]
        return self.author


class PostComment(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_comments")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by {self.author} on {self.post}"


class PostEngagement(models.Model):
    LIKE = "like"
    SHARE = "share"
    PIN = "pin"

    ENGAGEMENT_CHOICES = [
        (LIKE, "Like"),
        (SHARE, "Share"),
        (PIN, "Pin"),
    ]

    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="engagements")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_engagements")
    engagement_type = models.CharField(max_length=20, choices=ENGAGEMENT_CHOICES)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["post", "user", "engagement_type"], name="unique_post_engagement"),
        ]

    def __str__(self):
        return f"{self.user} {self.engagement_type} {self.post}"


class ContentReport(models.Model):
    SPAM = "spam"
    HARASSMENT = "harassment"
    HATE_SPEECH = "hate_speech"
    INAPPROPRIATE = "inappropriate"
    PAYMENT = "payment"
    OTHER = "other"

    PENDING = "pending"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    ESCALATED = "escalated"

    REASON_CHOICES = [
        (SPAM, "Spam"),
        (HARASSMENT, "Harassment"),
        (HATE_SPEECH, "Hate speech"),
        (INAPPROPRIATE, "Inappropriate content"),
        (PAYMENT, "Payment issue"),
        (OTHER, "Other"),
    ]
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (IN_REVIEW, "In review"),
        (RESOLVED, "Resolved"),
        (DISMISSED, "Dismissed"),
        (ESCALATED, "Escalated"),
    ]

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="submitted_reports",
        null=True,
        blank=True,
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="received_reports",
        null=True,
        blank=True,
    )
    community = models.ForeignKey(Community, on_delete=models.SET_NULL, related_name="reports", null=True, blank=True)
    post = models.ForeignKey(CommunityPost, on_delete=models.SET_NULL, related_name="reports", null=True, blank=True)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default=OTHER)
    description = models.TextField()
    ai_category = models.CharField(max_length=60, blank=True)
    ai_priority = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    resolution_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_reports",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ai_priority", "-created_at"]

    def __str__(self):
        return f"{self.get_reason_display()} report ({self.get_status_display()})"


class SubscriptionPlan(models.Model):
    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    community_limit = models.PositiveIntegerField(default=1)
    feature_summary = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "name"]

    def __str__(self):
        return self.name


class UserSubscription(models.Model):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (ACTIVE, "Active"),
        (EXPIRED, "Expired"),
        (CANCELLED, "Cancelled"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    community_limit_snapshot = models.PositiveIntegerField(default=1)
    auto_renew = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} - {self.plan}"


class PaymentTransaction(models.Model):
    ESEWA = "esewa"
    KHALTI = "khalti"
    MANUAL = "manual"

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"

    GATEWAY_CHOICES = [
        (ESEWA, "eSewa"),
        (KHALTI, "Khalti"),
        (MANUAL, "Manual"),
    ]
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SUCCESS, "Success"),
        (FAILED, "Failed"),
        (REFUNDED, "Refunded"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="transactions", null=True, blank=True)
    subscription = models.ForeignKey(
        UserSubscription,
        on_delete=models.SET_NULL,
        related_name="transactions",
        null=True,
        blank=True,
    )
    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES, default=MANUAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=SUCCESS)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference_code = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.amount} via {self.gateway}"


class Notification(models.Model):
    ACCOUNT = "account"
    COMMUNITY = "community"
    PAYMENT = "payment"
    MODERATION = "moderation"

    CATEGORY_CHOICES = [
        (ACCOUNT, "Account"),
        (COMMUNITY, "Community"),
        (PAYMENT, "Payment"),
        (MODERATION, "Moderation"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=140)
    body = models.TextField()
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default=ACCOUNT)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class CommunityEvent(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="events")
    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="organized_events", null=True)
    title = models.CharField(max_length=140)
    description = models.TextField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    link_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self):
        return self.title


class AuditLog(models.Model):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    SEVERITY_CHOICES = [
        (INFO, "Info"),
        (WARNING, "Warning"),
        (CRITICAL, "Critical"),
    ]

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="audit_logs", null=True, blank=True)
    action = models.CharField(max_length=120)
    target = models.CharField(max_length=160, blank=True)
    details = models.TextField(blank=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=INFO)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} ({self.severity})"


class ContactMessage(models.Model):
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"
