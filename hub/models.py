import mimetypes
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import slugify


POST_ATTACHMENT_ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "webp", "mp4", "webm", "mov", "m4v"]
POST_ATTACHMENT_MAX_SIZE = 50 * 1024 * 1024


class UserProfile(models.Model):
    PLATFORM_OWNER = "owner"
    PLATFORM_ADMIN = "admin"
    PLATFORM_MEMBER = "member"

    PLATFORM_ROLE_CHOICES = [
        (PLATFORM_OWNER, "Platform owner"),
        (PLATFORM_ADMIN, "Platform admin"),
        (PLATFORM_MEMBER, "Member"),
    ]

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

    PERSONALIZATION_DEFAULTS = {
        "theme_preference": THEME_SYSTEM,
        "accent_color": "indigo",
        "layout_density": "comfortable",
        "default_dashboard_section": "overview",
        "sidebar_default": "expanded",
        "feed_order": "personalized",
        "content_language": "en",
        "show_recommendations": True,
        "in_app_notifications": True,
        "email_notifications": True,
        "push_notifications": False,
        "notification_frequency": "immediate",
        "quiet_hours_enabled": False,
        "quiet_hours_start": "21:00",
        "quiet_hours_end": "08:00",
        "language": "en",
        "timezone": "Asia/Kathmandu",
        "date_format": "DD MMM YYYY",
        "time_format": "12",
        "text_size": "default",
        "high_contrast": False,
        "reduced_motion": False,
        "profile_visibility": "members",
        "message_permission": "members",
        "show_online_status": True,
        "show_activity": True,
        "search_visibility": True,
    }

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    platform_role = models.CharField(
        max_length=20,
        choices=PLATFORM_ROLE_CHOICES,
        default=PLATFORM_MEMBER,
        help_text="Platform-wide role. Community roles are managed separately on CommunityMembership.",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=30, choices=GENDER_CHOICES, default=PREFER_NOT_TO_SAY)
    phone_number = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True)
    interests = models.CharField(max_length=255, blank=True)
    profile_image = models.FileField(upload_to="profile_images/", blank=True)
    avatar_url = models.URLField(blank=True)
    theme_preference = models.CharField(max_length=20, choices=THEME_CHOICES, default=THEME_SYSTEM)
    personalization_preferences = models.JSONField(default=dict, blank=True)
    mfa_enabled = models.BooleanField(default=True)
    account_deletion_requested_at = models.DateTimeField(null=True, blank=True)
    email_verified = models.BooleanField(default=False)
    otp_code = models.CharField(max_length=6, blank=True)
    otp_created_at = models.DateTimeField(null=True, blank=True)
    kyc_verified = models.BooleanField(default=False)
    kyc_full_name = models.CharField(max_length=255, blank=True)
    kyc_national_id = models.CharField(max_length=50, blank=True)
    kyc_date_of_birth = models.DateField(null=True, blank=True)
    kyc_verified_at = models.DateTimeField(null=True, blank=True)

    # Presence & account verification (Discord-style)
    PRESENCE_ONLINE = "online"
    PRESENCE_IDLE = "idle"
    PRESENCE_DND = "dnd"
    PRESENCE_OFFLINE = "offline"

    PRESENCE_CHOICES = [
        (PRESENCE_ONLINE, "Online"),
        (PRESENCE_IDLE, "Idle"),
        (PRESENCE_DND, "Do Not Disturb"),
        (PRESENCE_OFFLINE, "Offline"),
    ]

    presence_status = models.CharField(
        max_length=20,
        choices=PRESENCE_CHOICES,
        default=PRESENCE_OFFLINE,
        help_text="Presence status shown on the account (online / idle / DND / offline).",
    )
    custom_status = models.CharField(
        max_length=120,
        blank=True,
        help_text="Rich presence / custom status text, e.g. 'Playing Valorant'.",
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    verification_level = models.PositiveSmallIntegerField(
        default=1,
        help_text="1=email, 2=email+phone, 3=phone+account age, 4=KYC verified.",
    )

    # Payment settings (Nepal)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_holder = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=60, blank=True)
    esewa_id = models.CharField(max_length=80, blank=True, verbose_name="eSewa ID")
    khalti_id = models.CharField(max_length=80, blank=True, verbose_name="Khalti ID")

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user} profile"

    @property
    def profile_image_url(self):
        if self.profile_image and self.profile_image.name:
            return self.profile_image.url
        return self.avatar_url

    def set_otp(self, code):
        self.otp_code = code
        self.otp_created_at = timezone.now()

    @property
    def personalization(self):
        """Return saved UI preferences with safe defaults for older accounts."""
        saved = self.personalization_preferences if isinstance(self.personalization_preferences, dict) else {}
        preferences = self.PERSONALIZATION_DEFAULTS.copy()
        preferences.update({key: value for key, value in saved.items() if key in preferences})
        # Keep the established theme column authoritative for profiles created
        # before the Personalization page existed.
        if "theme_preference" not in saved:
            preferences["theme_preference"] = self.theme_preference
        return preferences


class Community(models.Model):
    PUBLIC = "public"
    PRIVATE = "private"

    THEME_PRESET_CHOICES = [
        ("violet", "Violet night"),
        ("ocean", "Ocean blue"),
        ("sunset", "Sunset coral"),
        ("forest", "Forest green"),
    ]

    VISIBILITY_CHOICES = [
        (PUBLIC, "Public"),
        (PRIVATE, "Private"),
    ]

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField()
    rules = models.TextField(blank=True)
    topic = models.CharField(max_length=80, default="General")
    location = models.CharField(max_length=120, blank=True)
    tags = models.CharField(max_length=255, blank=True)
    interests = models.CharField(max_length=255, blank=True)
    cover_image = models.FileField(upload_to="community_covers/", blank=True)
    theme_preset = models.CharField(max_length=20, choices=THEME_PRESET_CHOICES, default="violet")
    membership_plan_name = models.CharField(max_length=100, blank=True, default="Community Access")
    membership_plan_description = models.TextField(blank=True)
    membership_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default=PUBLIC)
    allow_member_posts = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)
    verification_level = models.PositiveSmallIntegerField(
        default=1,
        help_text="Discord-style gate for new accounts: 1=email, 2=email+phone, 3=phone+account age, 4=KYC.",
    )
    is_listed = models.BooleanField(
        default=True,
        help_text="Approval flag for appearing in the public Discover directory.",
    )
    boost_count = models.PositiveIntegerField(default=0, help_text="Member-funded community boosts / perks level.")
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

    @property
    def cover_image_url(self):
        if self.cover_image and self.cover_image.name:
            return self.cover_image.url
        return ""

    @property
    def requires_payment(self):
        return (self.membership_price or Decimal("0.00")) > Decimal("0.00")

    @property
    def tag_list(self):
        return [tag.strip() for tag in self.tags.split(",") if tag.strip()]

    @property
    def interest_list(self):
        return [interest.strip() for interest in self.interests.split(",") if interest.strip()]


class CommunityMembership(models.Model):
    OWNER = "owner"
    MODERATOR = "moderator"
    MEMBER = "member"

    ROLE_CHOICES = [
        (OWNER, "Owner"),
        (MODERATOR, "Moderator"),
        (MEMBER, "Member"),
    ]

    # Community permissions are deliberately kept here so every view can use
    # the same policy instead of reproducing slightly different role checks.
    ROLE_PERMISSIONS = {
        OWNER: {
            "manage_settings", "manage_members", "manage_roles", "moderate_content",
            "manage_rooms", "manage_events", "view_analytics", "view_payments", "request_payout",
            "delete_community",
        },
        MODERATOR: {
            "moderate_content", "manage_rooms", "manage_events",
        },
        MEMBER: set(),
    }

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    # Discord/Circle-style per-member state
    ACTIVE = "active"
    MUTED = "muted"
    BANNED = "banned"
    PENDING = "pending"

    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (MUTED, "Muted"),
        (BANNED, "Banned"),
        (PENDING, "Pending"),
    ]

    nickname = models.CharField(
        max_length=120,
        blank=True,
        help_text="Per-community display name, different from the global profile name.",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    muted_until = models.DateTimeField(null=True, blank=True)
    ban_reason = models.TextField(blank=True)
    banned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="banned_memberships",
    )
    banned_at = models.DateTimeField(null=True, blank=True)
    self_removed_at = models.DateTimeField(null=True, blank=True)
    can_post_without_approval = models.BooleanField(
        default=True,
        help_text="If False, the member's posts require moderator approval in rooms that require it.",
    )
    last_active_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["community__name", "user__username"]
        constraints = [
            models.UniqueConstraint(fields=["community", "user"], name="unique_community_membership"),
        ]

    def __str__(self):
        return f"{self.user} in {self.community} ({self.role})"

    @property
    def display_name(self):
        return self.nickname or self.user.first_name or self.user.get_username().split("@")[0] or self.user.username

    @property
    def is_active_member(self):
        return self.status == self.ACTIVE

    @property
    def permission_set(self):
        return self.ROLE_PERMISSIONS.get(self.role, set())

    def has_permission(self, permission):
        return permission in self.permission_set

    def can_assign_role(self, target_role):
        """Only a community owner can grant or revoke moderator access."""
        if target_role == self.OWNER:
            return False
        return self.role == self.OWNER and target_role in {self.MODERATOR, self.MEMBER}

    @property
    def is_muted(self):
        if self.status != self.MUTED:
            return False
        if self.muted_until and self.muted_until > timezone.now():
            return True
        return False


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
    attachment_file = models.FileField(
        upload_to="community_attachments/",
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=POST_ATTACHMENT_ALLOWED_EXTENSIONS)],
    )
    attachment_url = models.URLField(blank=True)
    ai_summary = models.TextField(blank=True)
    safety_status = models.CharField(max_length=20, choices=SAFETY_STATUS_CHOICES, default=CLEAN)
    accept_donations = models.BooleanField(default=False, verbose_name="Accept donations")
    is_pinned = models.BooleanField(default=False)
    publish_at = models.DateTimeField(null=True, blank=True, help_text="Schedule post for future publishing")
    moderation_notes = models.TextField(blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Room-level feed + content approval workflow (Circle addition)
    room = models.ForeignKey(
        "CommunityRoom",
        on_delete=models.SET_NULL,
        related_name="posts",
        null=True,
        blank=True,
        help_text="Room this post belongs to (room-level content feed, separate from the global feed).",
    )
    APPROVAL_PENDING = "pending"
    APPROVAL_APPROVED = "approved"
    APPROVAL_REJECTED = "rejected"

    APPROVAL_STATUS_CHOICES = [
        (APPROVAL_PENDING, "Pending"),
        (APPROVAL_APPROVED, "Approved"),
        (APPROVAL_REJECTED, "Rejected"),
    ]

    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_STATUS_CHOICES,
        default=APPROVAL_APPROVED,
        help_text="Member-to-member content approval workflow scoped to a Room.",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_posts",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    moderation_reason = models.TextField(
        blank=True,
        help_text="Reason shown to the author when their content is actioned (audit-log-lite for users).",
    )
    is_edited = models.BooleanField(default=False)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def display_author(self):
        if self.author_user:
            return self.author_user.first_name or self.author_user.get_username().split("@")[0]
        return self.author

    @property
    def attachment_file_url(self):
        if self.attachment_file and self.attachment_file.name:
            return self.attachment_file.url
        return ""

    @property
    def attachment_display_url(self):
        return self.attachment_file_url or self.attachment_url

    @property
    def attachment_name(self):
        if self.attachment_file and self.attachment_file.name:
            return Path(self.attachment_file.name).name
        if self.attachment_url:
            path = urlparse(self.attachment_url).path
            return Path(path).name or "Attachment"
        return ""

    @property
    def attachment_mime_type(self):
        source = self.attachment_file.name if self.attachment_file and self.attachment_file.name else self.attachment_url
        if not source:
            return ""
        mime_type, _ = mimetypes.guess_type(source)
        return mime_type or ""

    @property
    def attachment_is_image(self):
        return self.attachment_mime_type.startswith("image/")

    @property
    def attachment_is_video(self):
        return self.attachment_mime_type.startswith("video/")


class PostComment(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_comments")
    body = models.TextField(blank=True)
    attachment_file = models.FileField(
        upload_to="community_attachments/",
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=POST_ATTACHMENT_ALLOWED_EXTENSIONS)],
    )
    attachment_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Threaded replies (Discord-style), edit history & moderation reason
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="replies",
        null=True,
        blank=True,
        help_text="Parent comment for threaded replies.",
    )
    level = models.PositiveIntegerField(default=0, help_text="Threading depth (0 = top-level comment).")
    is_edited = models.BooleanField(default=False)
    edited_at = models.DateTimeField(null=True, blank=True)
    moderation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by {self.author} on {self.post}"

    @property
    def attachment_file_url(self):
        if self.attachment_file and self.attachment_file.name:
            return self.attachment_file.url
        return ""

    @property
    def attachment_display_url(self):
        return self.attachment_file_url or self.attachment_url

    @property
    def attachment_name(self):
        if self.attachment_file and self.attachment_file.name:
            return Path(self.attachment_file.name).name
        if self.attachment_url:
            path = urlparse(self.attachment_url).path
            return Path(path).name or "Attachment"
        return ""

    @property
    def attachment_mime_type(self):
        source = self.attachment_file.name if self.attachment_file and self.attachment_file.name else self.attachment_url
        if not source:
            return ""
        mime_type, _ = mimetypes.guess_type(source)
        return mime_type or ""

    @property
    def attachment_is_image(self):
        return self.attachment_mime_type.startswith("image/")

    @property
    def attachment_is_video(self):
        return self.attachment_mime_type.startswith("video/")


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


class CommentEngagement(models.Model):
    LIKE = "like"
    LOVE = "love"
    LAUGH = "laugh"
    WOW = "wow"
    SAD = "sad"
    ANGRY = "angry"

    ENGAGEMENT_CHOICES = [
        (LIKE, "Like"),
        (LOVE, "Love"),
        (LAUGH, "Haha"),
        (WOW, "Wow"),
        (SAD, "Sad"),
        (ANGRY, "Angry"),
    ]

    comment = models.ForeignKey(PostComment, on_delete=models.CASCADE, related_name="engagements")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comment_engagements")
    engagement_type = models.CharField(max_length=20, choices=ENGAGEMENT_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["comment", "user", "engagement_type"], name="unique_comment_engagement"),
        ]

    def __str__(self):
        return f"{self.user} {self.engagement_type} {self.comment}"


class HiddenPost(models.Model):
    """Marks a post hidden for a specific user (used for 'delete for me')."""

    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="hidden_for")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hidden_posts")
    hidden_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-hidden_at"]
        constraints = [
            models.UniqueConstraint(fields=["post", "user"], name="unique_hidden_post_for_user"),
        ]

    def __str__(self):
        return f"{self.post} hidden for {self.user}"


class BookmarkedPost(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="bookmarked_by")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarked_posts")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["post", "user"], name="unique_bookmark"),
        ]

    def __str__(self):
        return f"{self.user} bookmarked {self.post}"


class UserFollow(models.Model):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (ACCEPTED, "Accepted"),
        (REJECTED, "Rejected"),
    ]

    follower = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following")
    followed = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="followers")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["follower", "followed"], name="unique_follow"),
        ]

    def __str__(self):
        return f"{self.follower} follows {self.followed}"


class UserReputation(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reputation")
    xp = models.PositiveIntegerField(default=0)
    level = models.PositiveIntegerField(default=1)
    posts_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)
    likes_received = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} Lv.{self.level} ({self.xp} XP)"

    def add_xp(self, amount):
        self.xp += amount
        new_level = self.xp // 100 + 1
        if new_level > self.level:
            self.level = new_level
        self.save(update_fields=["xp", "level"])


class Badge(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=60, default="fa-star")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserBadge(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="badges")
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="awarded_to")
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-awarded_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "badge"], name="unique_user_badge"),
        ]

    def __str__(self):
        return f"{self.user} × {self.badge}"


class PostPoll(models.Model):
    post = models.OneToOneField(CommunityPost, on_delete=models.CASCADE, related_name="poll")
    question = models.CharField(max_length=255)
    is_multiple_choice = models.BooleanField(default=False)
    closes_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.question


class PollOption(models.Model):
    poll = models.ForeignKey(PostPoll, on_delete=models.CASCADE, related_name="options")
    text = models.CharField(max_length=160)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.text


class PollVote(models.Model):
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="poll_votes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["option", "user"], name="unique_poll_vote"),
        ]

    def __str__(self):
        return f"{self.user} voted {self.option}"


class PostAward(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="awards")
    giver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="given_awards")
    name = models.CharField(max_length=60, default="Gold")
    icon = models.CharField(max_length=60, default="fa-trophy")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} award on {self.post}"


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
    
    # Subscription tier definitions and permissions
    subscription_tier = models.CharField(max_length=20, choices=[
        (BASIC, "Basic"),
        (STANDARD, "Standard"),
        (PREMIUM, "Premium"),
    ], default=BASIC)
    
    # Role-based access permissions
    can_create_communities = models.BooleanField(default=False)  # Basic access
    can_edit_communities = models.BooleanField(default=False)   # Standard+
    can_delete_communities = models.BooleanField(default=False) # Premium only
    can_manage_members = models.BooleanField(default=False)      # Standard+
    can_access_analytics = models.BooleanField(default=False)   # Premium+
    can_host_events = models.BooleanField(default=False)         # Standard+
    can_monetize_communities = models.BooleanField(default=False)  # Premium only
    can_use_advanced_themes = models.BooleanField(default=False)  # Premium+
    can_access_support = models.CharField(max_length=20, default="standard", choices=[
        ("none", "No Support"),
        ("standard", "Standard Support"),
        ("priority", "Priority Support"),
        ("dedicated", "Dedicated Support")
    ])
    max_community_memberships = models.PositiveIntegerField(default=1, help_text="Maximum communities user can create")
    max_community_posts_per_day = models.PositiveIntegerField(default=50, help_text="Maximum posts allowed per day")
    max_attachment_size = models.PositiveIntegerField(default=50, help_text="Maximum attachment size in MB")
    advanced_feature_access = models.JSONField(default=dict, help_text="JSON mapping of advanced features per tier")
    api_access_level = models.CharField(max_length=20, default="basic", choices=[
        ("basic", "Basic API"),
        ("standard", "Standard API"),
        ("advanced", "Advanced API"),
        ("unlimited", "Unlimited API")
    ])
    webhook_access = models.BooleanField(default=False)
    integrations_allowed = models.PositiveIntegerField(default=2)
    custom_domains = models.BooleanField(default=False)
    priority_support_hours = models.PositiveIntegerField(default=0, help_text="Hours of priority support per week")
    white_label_options = models.BooleanField(default=False)
    financial_reporting_access = models.BooleanField(default=False)
    marketing_tools_access = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "name"]
        permissions = [
            ("view_subscription_plan", "Can view subscription plan"),
            ("manage_subscription_plans", "Can manage subscription plans"),
        ]

    def __str__(self):
        return self.name
    
    def get_effective_permissions(self, user_subscription=None):
        """Return effective permissions for a user based on their subscription tier"""
        permissions = {
            'basic': {
                'can_create_communities': False,
                'can_edit_communities': False,
                'can_delete_communities': False,
                'can_manage_members': False,
                'can_access_analytics': False,
                'can_host_events': False,
                'can_monetize_communities': False,
                'can_use_advanced_themes': False,
                'max_communities': 0,
                'max_posts_per_day': 20,
            },
            'standard': {
                'can_create_communities': True,
                'can_edit_communities': True,
                'can_delete_communities': False,
                'can_manage_members': True,
                'can_access_analytics': False,
                'can_host_events': True,
                'can_monetize_communities': False,
                'can_use_advanced_themes': False,
                'max_communities': 5,
                'max_posts_per_day': 100,
            },
            'premium': {
                'can_create_communities': True,
                'can_edit_communities': True,
                'can_delete_communities': True,
                'can_manage_members': True,
                'can_access_analytics': True,
                'can_host_events': True,
                'can_monetize_communities': True,
                'can_use_advanced_themes': True,
                'max_communities': -1,  # Unlimited
                'max_posts_per_day': -1,  # Unlimited
            },
        }
        
        return permissions.get(self.subscription_tier, permissions['basic'])
    
    def upgrade_tier(self, new_tier):
        """Upgrade subscription tier with proper validation"""
        if new_tier == self.subscription_tier:
            return False, "Already on this tier"
        
        if new_tier == 'basic':
            return False, "Cannot downgrade premium subscriptions"
        
        if self.subscription_tier == 'premium' and new_tier == 'standard':
            return False, "Premium subscriptions can only be upgraded"
        
        self.subscription_tier = new_tier
        self.save()
        return True, f"Upgraded to {self.get_tier_display()} tier"
    
    def get_tier_display(self):
        return dict(self._meta.get_field('subscription_tier').choices)[self.subscription_tier]
    
    def check_user_permissions(self, user):
        """Check if a user has specific permissions for this plan"""
        # This will be overridden by UserSubscription for actual user checks
        return False


class SubscriptionTierLimits(models.Model):
    """Global tier limits and settings"""
    tier_name = models.CharField(max_length=20, choices=[
        (SubscriptionPlan.BASIC, "Basic"),
        (SubscriptionPlan.STANDARD, "Standard"),
        (SubscriptionPlan.PREMIUM, "Premium"),
    ])
    
    # Community creation limits
    max_communities_per_user = models.PositiveIntegerField(default=1)
    max_community_size = models.PositiveIntegerField(default=100000)  # Member limit per community
    
    # Content creation limits
    max_posts_per_day = models.PositiveIntegerField(default=50)
    max_attachments_per_post = models.PositiveIntegerField(default=5)
    max_attachment_size_mb = models.PositiveIntegerField(default=50)
    
    # Feature flags
    feature_flags = models.JSONField(default=dict)
    
    # Analytics and reporting
    analytics_access_level = models.CharField(max_length=20, default="basic", choices=[
        ("basic", "Basic Analytics"),
        ("standard", "Standard Analytics"),
        ("advanced", "Advanced Analytics"),
    ])
    
    # Support levels
    support_level = models.CharField(max_length=20, default="standard", choices=[
        ("none", "No Support"),
        ("standard", "Standard Support"),
        ("priority", "Priority Support"),
        ("dedicated", "Dedicated Support")
    ])
    
    # Integration capabilities
    max_integrations = models.PositiveIntegerField(default=2)
    webhook_support = models.BooleanField(default=False)
    api_access_level = models.CharField(max_length=20, default="basic", choices=[
        ("basic", "Basic API"),
        ("standard", "Standard API"),
        ("advanced", "Advanced API"),
    ])
    
    class Meta:
        unique_together = ['tier_name']
    
    def __str__(self):
        return f"{self.tier_name} Tier Limits"


class SubscriptionRole(models.Model):
    """Individual subscription-based roles and permissions"""
    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"
    
    ROLE_CHOICES = [
        (BASIC, "Basic User"),
        (STANDARD, "Standard User"),
        (PREMIUM, "Premium User"),
    ]
    
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription_role")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=BASIC)
    subscription_plan = models.ForeignKey(
        SubscriptionPlan, 
        on_delete=models.PROTECT, 
        null=True, 
        blank=True,
        help_text="Current subscription plan for this role"
    )
    role_permissions = models.JSONField(default=dict, help_text="Custom role-specific permissions")
    access_level = models.PositiveIntegerField(default=1, help_text="Access level (higher = more privileges)")
    is_active = models.BooleanField(default=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-access_level', 'user__username']
    
    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"
    
    def get_role_display(self):
        return dict(self.ROLE_CHOICES)[self.role]
    
    def has_permission(self, permission_name):
        """Check if user has specific permission based on role"""
        # First check role-level permissions
        if permission_name in self.role_permissions:
            return self.role_permissions[permission_name]
        
        # Then check subscription-plan based permissions
        if self.subscription_plan:
            plan_permissions = self.subscription_plan.get_effective_permissions(self)
            if permission_name in plan_permissions:
                return plan_permissions[permission_name]
        
        # Default role-based permissions
        basic_permissions = {
            'can_create_communities': self.role in [self.STANDARD, self.PREMIUM],
            'can_edit_communities': self.role in [self.STANDARD, self.PREMIUM],
            'can_manage_members': self.role in [self.STANDARD, self.PREMIUM],
            'can_host_events': self.role in [self.STANDARD, self.PREMIUM],
            'can_access_analytics': self.role == self.PREMIUM,
            'can_monetize_communities': self.role == self.PREMIUM,
        }
        return basic_permissions.get(permission_name, False)


class CommunitySubscriptionAccess(models.Model):
    """Track community-level subscription access and permissions"""
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="subscription_accesses")
    
    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"
    
    ACCESS_LEVEL_CHOICES = [
        (BASIC, "Basic Access"),
        (STANDARD, "Standard Access"),
        (PREMIUM, "Premium Access"),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_subscriptions")
    access_level = models.CharField(max_length=20, choices=ACCESS_LEVEL_CHOICES, default=BASIC)
    
    # Granular permissions for this community
    can_view_posts = models.BooleanField(default=True)
    can_create_posts = models.BooleanField(default=False)
    can_edit_posts = models.BooleanField(default=False)
    can_delete_posts = models.BooleanField(default=False)
    can_manage_members = models.BooleanField(default=False)
    can_view_analytics = models.BooleanField(default=False)
    can_host_events = models.BooleanField(default=False)
    can_monetize = models.BooleanField(default=False)
    can_use_advanced_features = models.BooleanField(default=False)
    
    # Member limits
    max_community_members = models.PositiveIntegerField(default=0)
    max_concurrent_members = models.PositiveIntegerField(default=0)
    
    joined_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(
        UserProfile, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="community_subscriptions_given"
    )
    
    class Meta:
        unique_together = ['community', 'user']
        ordering = ['-joined_at']
    
    def __str__(self):
        return f"{self.user.username} in {self.community.name} ({self.get_access_level_display()})"
    
    def get_access_level_display(self):
        return dict(self.ACCESS_LEVEL_CHOICES)[self.access_level]
    
    def has_access(self, permission_name):
        """Check if user has specific permission for this community"""
        permission_map = {
            'view_posts': 'can_view_posts',
            'create_posts': 'can_create_posts',
            'edit_posts': 'can_edit_posts',
            'delete_posts': 'can_delete_posts',
            'manage_members': 'can_manage_members',
            'view_analytics': 'can_view_analytics',
            'host_events': 'can_host_events',
            'monetize': 'can_monetize',
            'advanced_features': 'can_use_advanced_features',
        }
        
        field_name = permission_map.get(permission_name, None)
        if field_name:
            return getattr(self, field_name)
        return False
    
    def upgrade_access_level(self, new_level):
        """Upgrade user's access level for this community"""
        if new_level == self.access_level:
            return False, "Already at this access level"
        
        access_levels = {
            self.BASIC: 1,
            self.STANDARD: 2,
            self.PREMIUM: 3,
        }
        
        if access_levels[new_level] <= access_levels[self.access_level]:
            return False, "Cannot downgrade access level"
        
        self.access_level = new_level
        self.save()
        
        # Update granular permissions based on new access level
        self.update_granular_permissions(new_level)
        return True, f"Upgraded to {self.get_access_level_display()} access"
    
    def update_granular_permissions(self, access_level):
        """Update granular permissions based on access level"""
        if access_level >= 3:  # Premium
            self.can_create_posts = True
            self.can_edit_posts = True
            self.can_delete_posts = True
            self.can_manage_members = True
            self.can_view_analytics = True
            self.can_host_events = True
            self.can_monetize = True
            self.can_use_advanced_features = True
            self.max_community_members = 50000
        elif access_level == 2:  # Standard
            self.can_create_posts = True
            self.can_edit_posts = True
            self.can_delete_posts = False
            self.can_manage_members = True
            self.can_view_analytics = False
            self.can_host_events = True
            self.can_monetize = False
            self.can_use_advanced_features = False
            self.max_community_members = 10000
        else:  # Basic
            self.can_create_posts = False
            self.can_edit_posts = False
            self.can_delete_posts = False
            self.can_manage_members = False
            self.can_view_analytics = False
            self.can_host_events = False
            self.can_monetize = False
            self.can_use_advanced_features = False
            self.max_community_members = 1000
    
    def get_allowed_features(self):
        """Get list of features user can access in this community"""
        features = []
        
        if self.can_create_posts:
            features.append("create_posts")
        if self.can_edit_posts:
            features.append("edit_posts")
        if self.can_delete_posts:
            features.append("delete_posts")
        if self.can_manage_members:
            features.append("manage_members")
        if self.can_view_analytics:
            features.append("view_analytics")
        if self.can_host_events:
            features.append("host_events")
        if self.can_monetize:
            features.append("monetize")
        if self.can_use_advanced_features:
            features.append("advanced_features")
        
        return features


class SubscriptionActivityLog(models.Model):
    """Log subscription activities and permission changes"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription_activities")
    subscription_plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="activity_logs")
    action = models.CharField(max_length=50)
    details = models.JSONField(default=dict)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performed_subscription_activities"
    )
    performed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-performed_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.performed_at}"


class SubscriptionFeatureAccess(models.Model):
    """Track feature access by subscription tier"""
    feature_name = models.SlugField(max_length=100, unique=True)
    feature_category = models.CharField(max_length=50, choices=[
        ("community_management", "Community Management"),
        ("content_creation", "Content Creation"),
        ("analytics", "Analytics"),
        ("support", "Support"),
        ("integrations", "Integrations"),
        ("monetization", "Monetization"),
        ("advanced_features", "Advanced Features"),
        ("customization", "Customization"),
    ])
    description = models.TextField()
    
    # Access levels required
    basic_access = models.BooleanField(default=False)
    standard_access = models.BooleanField(default=False)
    premium_access = models.BooleanField(default=False)
    
    # Usage limits
    max_uses_per_month = models.PositiveIntegerField(default=0)
    max_uses_per_day = models.PositiveIntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ["feature_category", "feature_name"]
    
    def __str__(self):
        return f"{self.feature_name} ({self.feature_category})"
    
    def is_available_for_tier(self, tier):
        """Check if feature is available for specific tier"""
        return getattr(self, f"{tier}_access", False)
    
    def get_accessible_tiers(self):
        """Get list of tiers that have access to this feature"""
        tiers = []
        if self.basic_access:
            tiers.append('basic')
        if self.standard_access:
            tiers.append('standard')
        if self.premium_access:
            tiers.append('premium')
        return tiers


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
    community = models.ForeignKey(
        Community,
        on_delete=models.SET_NULL,
        related_name="payment_transactions",
        null=True,
        blank=True,
    )
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
    transaction_uuid = models.CharField(max_length=120, unique=True, null=True, blank=True)
    reference_code = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.amount} via {self.gateway}"


class PostDonation(models.Model):
    ESEWA = "esewa"
    KHALTI = "khalti"
    MANUAL = "manual"

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"

    GATEWAY_CHOICES = [
        (ESEWA, "eSewa"),
        (KHALTI, "Khalti"),
        (MANUAL, "Manual"),
    ]
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SUCCESS, "Success"),
        (FAILED, "Failed"),
    ]

    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="donations")
    donor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="donations")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES, default=MANUAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=SUCCESS)
    reference_code = models.CharField(max_length=120, blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.donor} donated {self.amount} to post {self.post_id} via {self.gateway}"


class PayoutRequest(models.Model):
    BANK = "bank"
    ESEWA = "esewa"
    KHALTI = "khalti"

    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    METHOD_CHOICES = [
        (BANK, "Bank account"),
        (ESEWA, "eSewa"),
        (KHALTI, "Khalti"),
    ]
    STATUS_CHOICES = [
        (PENDING, "Pending review"),
        (PROCESSING, "Processing"),
        (PAID, "Paid"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]

    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="payout_requests",
        null=True,
        blank=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_payout_requests",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("1000.00"))],
    )
    payout_method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    creator_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    reference_code = models.CharField(max_length=120, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_community_payouts",
    )

    class Meta:
        ordering = ["-requested_at"]

    def save(self, *args, **kwargs):
        if self.status in {self.PAID, self.REJECTED, self.CANCELLED} and not self.processed_at:
            self.processed_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.community.name} - Rs. {self.amount} ({self.get_status_display()})"


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

    LIKE = "like"
    COMMENT = "comment"
    MENTION = "mention"
    FOLLOW = "follow"
    AWARD = "award"
    EVENT = "event"
    JOB = "job"
    RESOURCE = "resource"
    LIVE = "live"
    ROOM = "room"
    GENERAL = "general"

    NOTIFICATION_TYPE_CHOICES = [
        (LIKE, "Like"),
        (COMMENT, "Comment"),
        (MENTION, "Mention"),
        (FOLLOW, "Follow"),
        (AWARD, "Award"),
        (EVENT, "Event"),
        (JOB, "Job"),
        (RESOURCE, "Resource"),
        (LIVE, "Live"),
        (ROOM, "Room"),
        (GENERAL, "General"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="triggered_notifications")
    community = models.ForeignKey("Community", on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications")
    room = models.ForeignKey("CommunityRoom", on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications")
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPE_CHOICES, default=GENERAL)
    title = models.CharField(max_length=140)
    body = models.TextField()
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default=ACCOUNT)
    is_read = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
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


class DirectMessageThread(models.Model):
    """Represents a conversation between two users or a user and admin."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    BLOCKED = "blocked"
    
    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (ARCHIVED, "Archived"),
        (BLOCKED, "Blocked"),
    ]
    
    participant1 = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="message_threads_as_p1"
    )
    participant2 = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="message_threads_as_p2"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ["-last_message_at", "-updated_at"]
        unique_together = [("participant1", "participant2")]
    
    def __str__(self):
        return f"Thread: {self.participant1.get_full_name()} <-> {self.participant2.get_full_name()}"


class DirectMessage(models.Model):
    """Individual message in a direct message thread."""
    thread = models.ForeignKey(
        DirectMessageThread,
        on_delete=models.CASCADE,
        related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages"
    )
    body = models.TextField()
    attachment = models.FileField(upload_to="message_attachments/", blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Message editing with visible edit history & read receipts (Discord-style)
    is_edited = models.BooleanField(default=False, help_text="Shows an '(edited)' marker on the message.")
    edited_at = models.DateTimeField(null=True, blank=True)
    read_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="read_messages",
        blank=True,
        help_text="Users who have read this message (read receipts / seen-by).",
    )
    edit_history = models.JSONField(default=list, blank=True, help_text="List of {edited_at, body} snapshots.")

    class Meta:
        ordering = ["created_at"]
    
    def __str__(self):
        return f"Message from {self.sender.get_full_name()} - {self.created_at}"


class CommunityRoom(models.Model):
    ICON_TYPE_SYSTEM = "system"
    ICON_TYPE_AI = "ai"
    ICON_TYPE_CUSTOM = "custom"

    ICON_TYPE_CHOICES = [
        (ICON_TYPE_SYSTEM, "System Icon"),
        (ICON_TYPE_AI, "AI Generated"),
        (ICON_TYPE_CUSTOM, "Custom Color"),
    ]

    ROOM_TYPE_ANNOUNCEMENTS = "announcements"
    ROOM_TYPE_CHAT = "chat"
    ROOM_TYPE_EVENTS = "events"
    ROOM_TYPE_RESOURCES = "resources"
    ROOM_TYPE_CERTIFICATIONS = "certifications"
    ROOM_TYPE_NETWORKING = "networking"
    ROOM_TYPE_JOBS = "jobs"
    ROOM_TYPE_LIVE = "live"
    ROOM_TYPE_TOPICS = "topics"
    ROOM_TYPE_CUSTOM = "custom"

    ROOM_TYPE_CHOICES = [
        (ROOM_TYPE_ANNOUNCEMENTS, "Announcements"),
        (ROOM_TYPE_CHAT, "Chat"),
        (ROOM_TYPE_EVENTS, "Events"),
        (ROOM_TYPE_RESOURCES, "Resources"),
        (ROOM_TYPE_CERTIFICATIONS, "Certifications"),
        (ROOM_TYPE_NETWORKING, "Networking"),
        (ROOM_TYPE_JOBS, "Jobs"),
        (ROOM_TYPE_LIVE, "Live Sessions"),
        (ROOM_TYPE_TOPICS, "Topics"),
        (ROOM_TYPE_CUSTOM, "Custom"),
    ]

    LAYOUT_BLANK = "blank"
    LAYOUT_CHAT = "chat"
    LAYOUT_FORUM = "forum"
    LAYOUT_RESOURCE = "resource"
    LAYOUT_JOB_BOARD = "job_board"
    LAYOUT_EVENT = "event"

    LAYOUT_CHOICES = [
        (LAYOUT_BLANK, "Blank"),
        (LAYOUT_CHAT, "Chat"),
        (LAYOUT_FORUM, "Forum"),
        (LAYOUT_RESOURCE, "Resource"),
        (LAYOUT_JOB_BOARD, "Job Board"),
        (LAYOUT_EVENT, "Event"),
    ]

    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="rooms",
    )
    name = models.CharField(max_length=100)
    room_type = models.CharField(max_length=30, choices=ROOM_TYPE_CHOICES, default=ROOM_TYPE_CUSTOM)
    layout = models.CharField(max_length=30, choices=LAYOUT_CHOICES, default=LAYOUT_BLANK)
    description = models.TextField(blank=True)
    icon_class = models.CharField(max_length=100, blank=True, help_text="Font Awesome class, e.g. fa-solid fa-bullhorn")
    icon_image_url = models.URLField(blank=True, help_text="AI-generated or custom icon URL")
    icon_type = models.CharField(max_length=20, choices=ICON_TYPE_CHOICES, default=ICON_TYPE_SYSTEM)
    color = models.CharField(max_length=20, blank=True, default="gray", help_text="Tailwind color name for bg/icon tint")
    display_order = models.PositiveIntegerField(default=0)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Room access control & rich room types
    OPEN = "open"
    INVITE_ONLY = "invite_only"
    APPROVAL_REQUIRED = "approval_required"
    PAYWALL = "paywall"

    ACCESS_CHOICES = [
        (OPEN, "Open"),
        (INVITE_ONLY, "Invite Only"),
        (APPROVAL_REQUIRED, "Approval Required"),
        (PAYWALL, "Paywall"),
    ]

    access_type = models.CharField(max_length=20, choices=ACCESS_CHOICES, default=OPEN)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"),
                                help_text="Paywall price for this individual room (free community can still have premium rooms).")
    is_adult_only = models.BooleanField(default=False, help_text="Age-restricted / explicit-content room gated behind age confirmation.")
    allow_member_posts = models.BooleanField(default=True, help_text="Per-room posting permission (independent of community setting).")
    requires_approval = models.BooleanField(default=False, help_text="Member posts in this room require moderator approval.")
    is_voice_enabled = models.BooleanField(default=False, help_text="Voice-enabled room (join/leave, mute/deafen, screen share).")
    is_stage_enabled = models.BooleanField(default=False, help_text="Live 'Stage' room: one-to-many audio with a moderated speaker queue.")
    voice_max_speakers = models.PositiveIntegerField(default=10)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["display_order", "name"]
        unique_together = [("community", "name")]

    def __str__(self):
        return f"{self.community.name} — {self.name}"

    @property
    def requires_access_control(self):
        return self.access_type in {self.INVITE_ONLY, self.APPROVAL_REQUIRED, self.PAYWALL}

    @property
    def is_paywalled(self):
        return self.access_type == self.PAYWALL and (self.price or Decimal("0.00")) > Decimal("0.00")


class CommunityLink(models.Model):
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="links",
    )
    title = models.CharField(max_length=120, blank=True)
    url = models.URLField()
    icon_class = models.CharField(max_length=100, blank=True, help_text="Font Awesome class")
    icon_image_url = models.URLField(blank=True)
    open_in_new_tab = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "title"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Roles & permissions (Part 4.9 — Circle + Discord hybrid)
# ---------------------------------------------------------------------------

ROLE_PERMISSION_FLAGS = [
    ("manage_rooms", "Manage rooms"),
    ("manage_links", "Manage links"),
    ("manage_members", "Manage members"),
    ("manage_roles", "Manage roles"),
    ("manage_invites", "Manage invites"),
    ("ban_members", "Ban members"),
    ("kick_members", "Kick members"),
    ("timeout_members", "Timeout members"),
    ("force_mute", "Force mute in voice rooms"),
    ("manage_posts", "Manage posts"),
    ("edit_posts", "Edit any post"),
    ("delete_posts", "Delete any post"),
    ("pin_posts", "Pin / feature posts"),
    ("approve_content", "Approve member content"),
    ("moderate_content", "Moderate flagged content"),
    ("manage_events", "Manage events"),
    ("host_events", "Host events"),
    ("view_analytics", "View analytics / insights"),
    ("manage_paywall", "Manage room paywalls & pricing"),
    ("manage_ai_agents", "Configure community AI agents"),
    ("manage_webhooks", "Manage integrations / webhooks"),
    ("manage_nicknames", "Manage member nicknames"),
    ("manage_voice", "Moderate voice/stage rooms"),
]


class CommunityRole(models.Model):
    """Custom Discord/Circle-style hierarchical role within a community."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=20, blank=True, default="gray", help_text="Tailwind color name for badges.")
    position = models.PositiveIntegerField(default=0, help_text="Higher position = more authority.")
    is_admin_flag = models.BooleanField(default=False, help_text="Discord 'Administrator' flag: grants everything, bypasses overrides.")
    is_assignable_by_self = models.BooleanField(default=False, help_text="Members can self-assign this role (interest roles).")
    is_default = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False, help_text="Owner/Admin/Moderator/Room-Lead built-in roles.")
    description = models.TextField(blank=True)
    permissions = models.JSONField(
        default=dict,
        help_text="Granular permission flags, e.g. {'delete_posts': True}.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-position", "name"]
        unique_together = [("community", "name")]

    def __str__(self):
        return f"{self.community.name} — {self.name}"

    def has_permission(self, perm):
        if self.is_admin_flag:
            return True
        if self.is_system and self.name.lower() == "owner":
            return True
        return bool(self.permissions.get(perm, False))


class CommunityRoleAssignment(models.Model):
    """Through model assigning roles to community memberships."""

    membership = models.ForeignKey(CommunityMembership, on_delete=models.CASCADE, related_name="role_assignments")
    role = models.ForeignKey(CommunityRole, on_delete=models.CASCADE, related_name="assignments")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_community_roles",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("membership", "role")]

    def __str__(self):
        return f"{self.membership.user.username} = {self.role.name}"


class RoomRolePermission(models.Model):
    """Room-level permission override with explicit-deny priority over role grants."""

    ALLOW = "allow"
    DENY = "deny"
    INHERIT = "inherit"

    VALUE_CHOICES = [
        (ALLOW, "Allow"),
        (DENY, "Deny (wins over role grant)"),
        (INHERIT, "Inherit"),
    ]

    room = models.ForeignKey(CommunityRoom, on_delete=models.CASCADE, related_name="role_permissions")
    role = models.ForeignKey(CommunityRole, on_delete=models.CASCADE, related_name="room_overrides")
    permission = models.CharField(max_length=40, choices=[(f, l) for f, l in ROLE_PERMISSION_FLAGS])
    value = models.CharField(max_length=20, choices=VALUE_CHOICES, default=INHERIT)

    class Meta:
        unique_together = [("room", "role", "permission")]

    def __str__(self):
        return f"{self.room.name} / {self.role.name} / {self.permission} = {self.value}"


class CommunityRoomMembership(models.Model):
    """Join/leave individual rooms independently (member in community but only a subset of rooms)."""

    room = models.ForeignKey(CommunityRoom, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="room_memberships")
    is_active = models.BooleanField(default=True)
    self_removed = models.BooleanField(default=False, help_text="Open rooms can be rejoined without re-approval.")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_room_members",
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-joined_at"]
        unique_together = [("room", "user")]

    def __str__(self):
        return f"{self.user.username} in room {self.room.name}"


class RoomAccessGroup(models.Model):
    """Access-group layer separate from moderation roles: controls which rooms a member can see."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="access_groups")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    rooms = models.ManyToManyField(CommunityRoom, blank=True, related_name="access_groups")
    members = models.ManyToManyField(
        CommunityMembership,
        blank=True,
        related_name="access_groups",
        through="RoomAccessGroupMember",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("community", "name")]

    def __str__(self):
        return f"{self.community.name} — {self.name}"


class RoomAccessGroupMember(models.Model):
    group = models.ForeignKey(RoomAccessGroup, on_delete=models.CASCADE, related_name="memberships")
    membership = models.ForeignKey(CommunityMembership, on_delete=models.CASCADE, related_name="group_memberships")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_group_members",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("group", "membership")]

    def __str__(self):
        return f"{self.membership.user.username} in {self.group.name}"


# ---------------------------------------------------------------------------
# Custom profile fields per community (Circle addition)
# ---------------------------------------------------------------------------


class CommunityProfileFieldDefinition(models.Model):
    TEXT = "text"
    URL = "url"
    SOCIAL = "social"
    LOCATION = "location"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"

    FIELD_TYPE_CHOICES = [
        (TEXT, "Text"),
        (URL, "Website / URL"),
        (SOCIAL, "Social link"),
        (LOCATION, "Location"),
        (NUMBER, "Number"),
        (DATE, "Date"),
        (SELECT, "Select"),
    ]

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="profile_field_definitions")
    key = models.SlugField(max_length=60)
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default=TEXT)
    placeholder = models.CharField(max_length=200, blank=True)
    options = models.JSONField(default=list, blank=True, help_text="Choices for 'select' fields.")
    is_required = models.BooleanField(default=False, help_text="Required before a new member can enter the community.")
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "label"]
        unique_together = [("community", "key")]

    def __str__(self):
        return f"{self.community.name} — {self.label}"


class CommunityProfileFieldValue(models.Model):
    field = models.ForeignKey(CommunityProfileFieldDefinition, on_delete=models.CASCADE, related_name="values")
    membership = models.ForeignKey(CommunityMembership, on_delete=models.CASCADE, related_name="profile_field_values")
    value = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("field", "membership")]

    def __str__(self):
        return f"{self.membership.user.username} — {self.field.label}: {self.value[:40]}"


# ---------------------------------------------------------------------------
# Onboarding & invite links (Circle addition)
# ---------------------------------------------------------------------------


class OnboardingFlow(models.Model):
    community = models.OneToOneField(Community, on_delete=models.CASCADE, related_name="onboarding")
    is_enabled = models.BooleanField(default=False)
    welcome_message = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True, help_text="Ordered list of welcome steps {title, body}.")
    required_field_keys = models.JSONField(default=list, blank=True, help_text="Custom profile fields required before entry.")
    show_popup = models.BooleanField(default=False, help_text="Show an onboarding popup on entry.")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Onboarding: {self.community.name}"


class InviteLink(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="invite_links")
    code = models.CharField(max_length=40, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_invite_links",
    )
    is_active = models.BooleanField(default=True)
    is_vanity = models.BooleanField(default=False, help_text="Branded custom invite URL.")
    max_uses = models.PositiveIntegerField(default=0, help_text="0 = unlimited.")
    used_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    onboarding_override = models.JSONField(default=dict, blank=True, help_text="Entry-point-specific onboarding settings.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.community.name} — invite/{self.code}"

    @property
    def is_valid(self):
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        if self.max_uses and self.used_count >= self.max_uses:
            return False
        return True


class InterestTag(models.Model):
    """Self-assignable interest roles/tags (opt-in, not admin-assigned)."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="interest_tags")
    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=70)
    color = models.CharField(max_length=20, blank=True, default="blue")
    is_self_assignable = models.BooleanField(default=True)
    members = models.ManyToManyField(CommunityMembership, blank=True, related_name="interest_tags")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("community", "slug")]

    def __str__(self):
        return f"{self.community.name} — #{self.name}"


# ---------------------------------------------------------------------------
# Courses & certificates (Part 5.2 — highest-leverage gap)
# ---------------------------------------------------------------------------


class Course(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="courses")
    room = models.ForeignKey(
        CommunityRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courses",
        help_text="Course room this belongs to.",
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    cover_image = models.FileField(upload_to="course_covers/", blank=True)
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instructed_courses",
    )
    is_published = models.BooleanField(default=False)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=8, default="NPR")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def lesson_count(self):
        return self.lessons.count()

    @property
    def total_duration_seconds(self):
        total = self.lessons.aggregate(total=Sum("duration_seconds"))["total"] or 0
        return total


class Lesson(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=160)
    content = models.TextField(blank=True)
    video_url = models.URLField(blank=True)
    video_file = models.FileField(upload_to="lesson_videos/", blank=True)
    lesson_type = models.CharField(max_length=30, default="video", help_text="video / text / quiz / resource")
    duration_seconds = models.PositiveIntegerField(default=0)
    display_order = models.PositiveIntegerField(default=0)
    is_free_preview = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.course.title} — {self.title}"


class LessonProgress(models.Model):
    """Course completion tracking for members."""

    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lesson_progress")
    is_completed = models.BooleanField(default=False)
    progress_percent = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("lesson", "user")]

    def __str__(self):
        return f"{self.user.username} — {self.lesson.title} ({self.progress_percent}%)"


class Certificate(models.Model):
    """Downloadable completion certificate for a course."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="certificates")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="certificates")
    certificate_code = models.CharField(max_length=64, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    pdf_url = models.URLField(blank=True, help_text="Downloadable certificate file/URL.")

    class Meta:
        ordering = ["-issued_at"]
        unique_together = [("course", "user")]

    def __str__(self):
        return f"{self.user.username} — {self.course.title}"


class EventRSVP(models.Model):
    ATTENDING = "attending"
    NOT_ATTENDING = "not_attending"
    MAYBE = "maybe"

    STATUS_CHOICES = [
        (ATTENDING, "Attending"),
        (NOT_ATTENDING, "Not attending"),
        (MAYBE, "Maybe"),
    ]

    event = models.ForeignKey(CommunityEvent, on_delete=models.CASCADE, related_name="rsvps")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_rsvps")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ATTENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("event", "user")]

    def __str__(self):
        return f"{self.user.username} → {self.event.title} ({self.status})"


# ---------------------------------------------------------------------------
# AI agents, voice & stage rooms (Circle / Discord additions)
# ---------------------------------------------------------------------------


class AIAgent(models.Model):
    """Community AI agent that guides/coaches members within a Room."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="ai_agents")
    room = models.ForeignKey(
        CommunityRoom,
        on_delete=models.CASCADE,
        related_name="ai_agents",
        null=True,
        blank=True,
        help_text="Room the agent is scoped to (None = community-wide).",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    system_prompt = models.TextField(blank=True)
    avatar_url = models.URLField(blank=True)
    scopes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.community.name})"


class RoomVoiceSession(models.Model):
    """Live voice presence: join/leave, mute/deafen, screen share, stage speaker."""

    room = models.ForeignKey(CommunityRoom, on_delete=models.CASCADE, related_name="voice_sessions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="voice_sessions")
    is_muted = models.BooleanField(default=False)
    is_deafened = models.BooleanField(default=False)
    is_screen_sharing = models.BooleanField(default=False)
    is_stage_speaker = models.BooleanField(default=False)
    is_live = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-joined_at"]

    def __str__(self):
        return f"{self.user.username} in voice {self.room.name}"


class StageSpeakerQueue(models.Model):
    """Moderated speaker queue for one-to-many 'Stage' rooms."""

    REQUESTED = "requested"
    APPROVED = "approved"
    SPEAKING = "speaking"
    DENIED = "denied"

    STATUS_CHOICES = [
        (REQUESTED, "Requested"),
        (APPROVED, "Approved"),
        (SPEAKING, "Speaking"),
        (DENIED, "Denied"),
    ]

    room = models.ForeignKey(CommunityRoom, on_delete=models.CASCADE, related_name="speaker_queue")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stage_requests")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=REQUESTED)
    position = models.PositiveIntegerField(default=0)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stage_decisions",
    )

    class Meta:
        ordering = ["position", "requested_at"]

    def __str__(self):
        return f"{self.user.username} → stage {self.room.name} ({self.status})"


class CustomEmoji(models.Model):
    """Custom/animated emoji reactions per community (beyond the fixed reaction set)."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="custom_emojis")
    name = models.CharField(max_length=40, help_text="Emoji name used like :name:")
    image_url = models.URLField(blank=True)
    image_file = models.FileField(upload_to="custom_emojis/", blank=True)
    is_animated = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_emojis",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("community", "name")]

    def __str__(self):
        return f":{self.name}: ({self.community.name})"


class SlashCommand(models.Model):
    """Slash-command / bot-command interface for user-triggered actions (e.g. /poll, /remind)."""

    name = models.CharField(max_length=40, unique=True, help_text="Command name without the leading slash.")
    description = models.TextField(blank=True)
    handler = models.CharField(max_length=80, help_text="Python handler identifier.")
    is_active = models.BooleanField(default=True)
    cooldown_seconds = models.PositiveIntegerField(default=0)
    requires_kyc = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"/{self.name}"


# ---------------------------------------------------------------------------
# Trust, moderation & reporting
# ---------------------------------------------------------------------------


class RulesAcknowledgment(models.Model):
    """Users view community rules pinned/enforced at entry and must acknowledge them."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="rules_acknowledgments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rules_acknowledgments")
    rules_version = models.PositiveIntegerField(default=1)
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("community", "user")]

    def __str__(self):
        return f"{self.user.username} accepted rules of {self.community.name}"


class ContentActionLog(models.Model):
    """Audit-log-lite for users: see when their own content was actioned with a reason."""

    REMOVED = "removed"
    WARNED = "warned"
    MUTED = "muted"
    FLAGGED = "flagged"

    ACTION_CHOICES = [
        (REMOVED, "Removed"),
        (WARNED, "Warned"),
        (MUTED, "Muted"),
        (FLAGGED, "Flagged"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="content_action_logs")
    post = models.ForeignKey(CommunityPost, on_delete=models.SET_NULL, null=True, blank=True, related_name="action_logs")
    comment = models.ForeignKey(PostComment, on_delete=models.SET_NULL, null=True, blank=True, related_name="action_logs")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    reason = models.TextField(blank=True)
    acted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_actions_taken",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} content {self.action}"


class ContentApproval(models.Model):
    """Member-to-member content approval workflow scoped to a Room."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="approvals")
    room = models.ForeignKey(CommunityRoom, on_delete=models.CASCADE, related_name="content_approvals", null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="content_approvals_requested",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_approvals_reviewed",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Approval for {self.post} ({self.status})"


class ProfanityRule(models.Model):
    """Configurable automated content/profanity filter (manual moderation is separate)."""

    FLAG = "flag"
    REMOVE = "remove"

    ACTION_CHOICES = [
        (FLAG, "Flag for review"),
        (REMOVE, "Auto-remove"),
    ]

    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="profanity_rules",
        help_text="Leave blank for a platform-wide rule.",
    )
    pattern = models.CharField(max_length=120, help_text="Word or regex pattern to filter.")
    replacement = models.CharField(max_length=120, blank=True, default="***")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, default=FLAG)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profanity_rules_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pattern"]

    def __str__(self):
        return f"{self.pattern} → {self.action}"


class ModerationAction(models.Model):
    """Audited member management: kick, ban, timeout, force-mute, disconnect."""

    KICK = "kick"
    BAN = "ban"
    TIMEOUT = "timeout"
    MUTE = "mute"
    FORCE_MUTE = "force_mute"
    DISCONNECT = "disconnect"
    NICKNAME = "nickname"

    ACTION_CHOICES = [
        (KICK, "Kick"),
        (BAN, "Ban"),
        (TIMEOUT, "Timeout"),
        (MUTE, "Mute"),
        (FORCE_MUTE, "Force mute"),
        (DISCONNECT, "Disconnect"),
        (NICKNAME, "Nickname changed"),
    ]

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="moderation_actions")
    membership = models.ForeignKey(
        CommunityMembership,
        on_delete=models.CASCADE,
        related_name="moderation_actions",
        null=True,
        blank=True,
    )
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="moderation_actions")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    reason = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField(default=0, help_text="0 = indefinite (for timeout/mute).")
    expires_at = models.DateTimeField(null=True, blank=True)
    acted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_actions_taken",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.target_user.username} in {self.community.name}"


class AutoRoleRule(models.Model):
    """Automation: auto-role assignment when a member joins or matches a condition."""

    ON_JOIN = "on_join"
    ON_ROOM_JOIN = "on_room_join"
    CONDITION = "condition"

    TRIGGER_CHOICES = [
        (ON_JOIN, "On community join"),
        (ON_ROOM_JOIN, "On room join"),
        (CONDITION, "On matching condition"),
    ]

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="auto_role_rules")
    trigger = models.CharField(max_length=20, choices=TRIGGER_CHOICES, default=ON_JOIN)
    role = models.ForeignKey(CommunityRole, on_delete=models.CASCADE, related_name="auto_role_rules")
    room = models.ForeignKey(
        CommunityRoom,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="auto_role_rules",
        help_text="Required when trigger = on_room_join.",
    )
    condition_value = models.CharField(max_length=160, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["trigger", "role"]

    def __str__(self):
        return f"{self.community.name}: {self.trigger} → {self.role.name}"


class Announcement(models.Model):
    """Platform-wide or community-wide announcements sent by admins."""

    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="announcements",
        help_text="Leave blank for a platform-wide announcement.",
    )
    title = models.CharField(max_length=140)
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="announcements",
    )
    is_pinned = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return self.title


class EmailBroadcast(models.Model):
    """Email broadcasts/newsletters to members."""

    DRAFT = "draft"
    QUEUED = "queued"
    SENT = "sent"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (QUEUED, "Queued"),
        (SENT, "Sent"),
    ]

    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="email_broadcasts",
        help_text="Leave blank for a platform-wide newsletter.",
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_broadcasts_sent",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    recipients_count = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.subject


class Integration(models.Model):
    """Third-party tool hooks / webhooks per community."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="integrations")
    name = models.CharField(max_length=100)
    webhook_url = models.URLField(blank=True)
    api_key = models.CharField(max_length=255, blank=True)
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.community.name})"


class CommunityBoost(models.Model):
    """Member-funded community upgrades/perks (server-boosting style)."""

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name="boosts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_boosts")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} boosted {self.community.name}"
