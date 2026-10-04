from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from .models import (
    Community,
    CommunityLink,
    CommunityMembership,
    CommunityPost,
    CommunityRoom,
    ContactMessage,
    ContentReport,
    PostComment,
    SubscriptionPlan,
    UserProfile,
)


FIELD_CLASS = (
    "w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm "
    "outline-none transition focus:border-purple-400 focus:ring-4 focus:ring-purple-100"
)


class CommunityForm(forms.ModelForm):
    MAX_COVER_IMAGE_SIZE = 3 * 1024 * 1024

    class Meta:
        model = Community
        fields = [
            "name",
            "topic",
            "location",
            "tags",
            "interests",
            "visibility",
            "cover_image",
            "theme_preset",
            "description",
            "rules",
            "membership_plan_name",
            "membership_price",
            "membership_plan_description",
            "allow_member_posts",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Community name", "class": FIELD_CLASS}),
            "topic": forms.TextInput(attrs={"placeholder": "Topic", "class": FIELD_CLASS}),
            "location": forms.TextInput(attrs={"placeholder": "Location", "class": FIELD_CLASS}),
            "tags": forms.TextInput(attrs={"placeholder": "photography, workshops, beginners", "class": FIELD_CLASS}),
            "interests": forms.TextInput(attrs={"placeholder": "Lighting, portraits, travel shoots", "class": FIELD_CLASS}),
            "visibility": forms.Select(attrs={"class": FIELD_CLASS}),
            "cover_image": forms.FileInput(attrs={"accept": "image/*", "class": "sr-only"}),
            "theme_preset": forms.Select(attrs={"class": FIELD_CLASS}),
            "description": forms.Textarea(
                attrs={
                    "placeholder": "What is this community about?",
                    "rows": 4,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            ),
            "rules": forms.Textarea(
                attrs={
                    "placeholder": "Community rules",
                    "rows": 3,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            ),
            "membership_plan_name": forms.TextInput(
                attrs={"placeholder": "Community Access", "class": FIELD_CLASS}
            ),
            "membership_price": forms.NumberInput(
                attrs={"placeholder": "Join price", "min": "0", "step": "0.01", "class": FIELD_CLASS}
            ),
            "membership_plan_description": forms.Textarea(
                attrs={
                    "placeholder": "What does a paid member get?",
                    "rows": 3,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            ),
            "allow_member_posts": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "theme_preset" in self.fields:
            self.fields["theme_preset"].required = False

    def clean_theme_preset(self):
        value = self.cleaned_data.get("theme_preset")
        return value or getattr(self.instance, "theme_preset", "violet") or "violet"

    def clean_cover_image(self):
        image = self.cleaned_data.get("cover_image")
        if not image:
            return image

        content_type = getattr(image, "content_type", "")
        if content_type and not content_type.startswith("image/"):
            raise forms.ValidationError("Please upload an image file.")

        if image.size > self.MAX_COVER_IMAGE_SIZE:
            raise forms.ValidationError("Community photo must be 3 MB or smaller.")

        return image

    def clean_membership_plan_name(self):
        return self.cleaned_data.get("membership_plan_name", "").strip() or "Community Access"

    def clean_membership_price(self):
        price = self.cleaned_data.get("membership_price")
        if price in {None, ""}:
            return Decimal("0.00")
        if price < Decimal("0.00"):
            raise forms.ValidationError("Join price cannot be negative.")
        return price


class CommunityCreateForm(CommunityForm):
    """The intentionally small form used when a member launches a community.

    Everything beyond the core identity of a community is configured after it is
    created, from the community settings page.
    """

    class Meta(CommunityForm.Meta):
        fields = ["name", "topic", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["topic"].required = False
        self.fields["topic"].initial = "General"
        self.fields["topic"].widget.attrs["placeholder"] = "For example: Photography"

    def clean_topic(self):
        return self.cleaned_data.get("topic", "").strip() or "General"


class AdminUserCreationForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={"placeholder": "Temporary password", "class": FIELD_CLASS}
        )
    )
    is_staff = forms.BooleanField(required=False)
    is_superuser = forms.BooleanField(required=False)
    platform_role = forms.ChoiceField(
        choices=UserProfile.PLATFORM_ROLE_CHOICES,
        initial=UserProfile.PLATFORM_MEMBER,
        required=False,
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )

    class Meta:
        model = get_user_model()
        fields = ["first_name", "username", "email", "password", "is_staff", "is_superuser", "is_active"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "Full name", "class": FIELD_CLASS}),
            "username": forms.TextInput(attrs={"placeholder": "Username", "class": FIELD_CLASS}),
            "email": forms.EmailInput(attrs={"placeholder": "Email address", "class": FIELD_CLASS}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
            "is_staff": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
            "is_superuser": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
        }

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.set_password(self.cleaned_data["password"])
        if user.is_superuser:
            user.is_staff = True
        if commit:
            user.save()
        return user


class AdminCommunityForm(CommunityForm):
    class Meta(CommunityForm.Meta):
        fields = [
            "name",
            "topic",
            "location",
            "tags",
            "interests",
            "visibility",
            "owner",
            "cover_image",
            "description",
            "rules",
            "membership_plan_name",
            "membership_price",
            "membership_plan_description",
            "allow_member_posts",
        ]
        widgets = {
            **CommunityForm.Meta.widgets,
            "owner": forms.Select(attrs={"class": FIELD_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = get_user_model().objects.order_by("first_name", "email", "username")


class CommunityPostForm(forms.ModelForm):
    class Meta:
        model = CommunityPost
        fields = ["community", "title", "category", "content_type", "body", "attachment_file", "attachment_url", "accept_donations"]
        widgets = {
            "community": forms.Select(attrs={"class": FIELD_CLASS}),
            "title": forms.TextInput(
                attrs={
                    "placeholder": "Post title",
                    "class": FIELD_CLASS,
                }
            ),
            "category": forms.TextInput(
                attrs={
                    "placeholder": "Category",
                    "class": FIELD_CLASS,
                }
            ),
            "content_type": forms.Select(attrs={"class": FIELD_CLASS}),
            "body": forms.Textarea(
                attrs={
                    "placeholder": "Share an update with the community...",
                    "rows": 4,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            ),
            "attachment_file": forms.FileInput(
                attrs={"accept": "image/*,video/*", "class": FIELD_CLASS},
            ),
            "attachment_url": forms.URLInput(attrs={"placeholder": "Attachment URL (optional)", "class": FIELD_CLASS}),
            "accept_donations": forms.CheckboxInput(attrs={"class": "rounded border-slate-300 text-purple-600 focus:ring-purple-500"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        communities = Community.objects.none()
        if user and user.is_authenticated:
            communities = Community.objects.filter(memberships__user=user).order_by("name")
        self.fields["community"].queryset = communities
        self.fields["community"].empty_label = "Choose a community"
        self.fields["category"].required = False
        self.fields["content_type"].required = False
        self.fields["attachment_file"].required = False
        self.fields["attachment_url"].required = False

    def clean_category(self):
        return self.cleaned_data["category"].strip() or "General"

    def clean_content_type(self):
        return self.cleaned_data.get("content_type") or CommunityPost.UPDATE

    def clean_community(self):
        community = self.cleaned_data.get("community")
        if community is None:
            return community
        if not CommunityMembership.objects.filter(community=community, user=self.user).exists():
            raise forms.ValidationError("Join this community before posting.")
        return community

    @property
    def user(self):
        return getattr(self, "_user", None)

    @user.setter
    def user(self, value):
        self._user = value


class AdminCommunityPostForm(forms.ModelForm):
    class Meta:
        model = CommunityPost
        fields = [
            "community",
            "author_user",
            "title",
            "category",
            "content_type",
            "body",
            "attachment_file",
            "attachment_url",
            "is_pinned",
        ]
        widgets = {
            "community": forms.Select(attrs={"class": FIELD_CLASS}),
            "author_user": forms.Select(attrs={"class": FIELD_CLASS}),
            "title": forms.TextInput(attrs={"placeholder": "Post title", "class": FIELD_CLASS}),
            "category": forms.TextInput(attrs={"placeholder": "Category", "class": FIELD_CLASS}),
            "content_type": forms.Select(attrs={"class": FIELD_CLASS}),
            "body": forms.Textarea(
                attrs={
                    "placeholder": "Publish an update for a community...",
                    "rows": 4,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            ),
            "attachment_file": forms.FileInput(
                attrs={"accept": "image/*,video/*", "class": FIELD_CLASS},
            ),
            "attachment_url": forms.URLInput(attrs={"placeholder": "Attachment URL (optional)", "class": FIELD_CLASS}),
            "is_pinned": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["community"].queryset = Community.objects.order_by("name")
        self.fields["community"].empty_label = "Choose a community"
        self.fields["author_user"].queryset = get_user_model().objects.filter(is_active=True).order_by(
            "first_name", "email", "username"
        )
        self.fields["author_user"].empty_label = "Choose an author"
        self.fields["category"].required = False
        self.fields["content_type"].required = False
        self.fields["attachment_file"].required = False
        self.fields["attachment_url"].required = False

    def clean_category(self):
        return self.cleaned_data["category"].strip() or "General"

    def clean_content_type(self):
        return self.cleaned_data.get("content_type") or CommunityPost.UPDATE

    def save(self, commit=True):
        post = super().save(commit=False)
        author_user = self.cleaned_data.get("author_user")
        if author_user:
            post.author = author_user.first_name or author_user.get_username().split("@")[0]
        if commit:
            post.save()
        return post


class UserProfileForm(forms.ModelForm):
    MAX_PROFILE_IMAGE_SIZE = 2 * 1024 * 1024

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Username", "class": FIELD_CLASS}),
    )
    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Full name", "class": FIELD_CLASS}),
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"placeholder": "Email address", "class": FIELD_CLASS}),
    )

    class Meta:
        model = UserProfile
        fields = ["username", "first_name", "email", "phone_number", "address", "bio", "interests", "profile_image", "theme_preference"]
        widgets = {
            "phone_number": forms.TextInput(attrs={"placeholder": "Phone number", "class": FIELD_CLASS}),
            "address": forms.TextInput(attrs={"placeholder": "Address", "class": FIELD_CLASS}),
            "bio": forms.Textarea(attrs={"placeholder": "Short bio", "rows": 3, "class": f"{FIELD_CLASS} resize-none"}),
            "interests": forms.TextInput(attrs={"placeholder": "Interests, separated by commas", "class": FIELD_CLASS}),
            "profile_image": forms.FileInput(attrs={"accept": "image/*", "class": FIELD_CLASS}),
            "theme_preference": forms.Select(attrs={"class": FIELD_CLASS}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            self.fields["username"].initial = user.username
            self.fields["first_name"].initial = user.first_name
            self.fields["email"].initial = user.email

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            raise forms.ValidationError("Username is required.")

        User = get_user_model()
        username_field = User._meta.get_field(User.USERNAME_FIELD)
        for validator in username_field.validators:
            validator(username)

        queryset = User.objects.filter(username__iexact=username)
        if self.user is not None:
            queryset = queryset.exclude(pk=self.user.pk)
        if queryset.exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if not email:
            return email
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("This email is already used by another account.")
        return email

    def clean_profile_image(self):
        image = self.cleaned_data.get("profile_image")
        if not image:
            return image

        content_type = getattr(image, "content_type", "")
        if content_type and not content_type.startswith("image/"):
            raise forms.ValidationError("Please upload an image file.")

        if image.size > self.MAX_PROFILE_IMAGE_SIZE:
            raise forms.ValidationError("Profile image must be 2 MB or smaller.")

        return image

    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.user is not None:
            self.user.username = self.cleaned_data.get("username", "").strip()
            self.user.first_name = self.cleaned_data.get("first_name", "").strip()
            self.user.email = self.cleaned_data.get("email", "").strip().lower()
            if commit:
                self.user.save(update_fields=["username", "first_name", "email"])
        if commit:
            profile.save()
        return profile


class PersonalizationForm(forms.Form):
    """Validated, account-scoped preferences stored in UserProfile JSON."""

    SELECT_CLASS = (
        "w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 "
        "outline-none transition focus:border-gray-400 focus:ring-4 focus:ring-gray-100"
    )
    CHECKBOX_CLASS = "h-4 w-4 rounded border-gray-300 text-gray-900 focus:ring-gray-300"

    theme_preference = forms.ChoiceField(
        choices=UserProfile.THEME_CHOICES,
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    accent_color = forms.ChoiceField(
        choices=[("indigo", "Indigo"), ("violet", "Violet"), ("emerald", "Emerald"), ("rose", "Rose")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    layout_density = forms.ChoiceField(
        choices=[("comfortable", "Comfortable"), ("compact", "Compact")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    default_dashboard_section = forms.ChoiceField(
        choices=[
            ("overview", "Home"),
            ("discover", "Discover"),
            ("communities", "Communities"),
            ("messages", "Messages"),
        ],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    sidebar_default = forms.ChoiceField(
        choices=[("expanded", "Expanded"), ("collapsed", "Collapsed")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    feed_order = forms.ChoiceField(
        choices=[("personalized", "Personalized"), ("newest", "Newest first"), ("popular", "Most popular")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    content_language = forms.ChoiceField(
        choices=[("en", "English"), ("ne", "Nepali"), ("hi", "Hindi")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    show_recommendations = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))

    in_app_notifications = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))
    email_notifications = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))
    push_notifications = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))
    notification_frequency = forms.ChoiceField(
        choices=[("immediate", "Immediately"), ("daily", "Daily summary"), ("weekly", "Weekly summary"), ("off", "Off")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    quiet_hours_enabled = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))
    quiet_hours_start = forms.TimeField(
        input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"class": SELECT_CLASS, "type": "time"}),
    )
    quiet_hours_end = forms.TimeField(
        input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"class": SELECT_CLASS, "type": "time"}),
    )

    language = forms.ChoiceField(
        choices=[("en", "English"), ("ne", "Nepali"), ("hi", "Hindi")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    timezone = forms.ChoiceField(
        choices=[("Asia/Kathmandu", "Kathmandu (GMT+5:45)"), ("UTC", "UTC"), ("Asia/Kolkata", "India (GMT+5:30)")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    date_format = forms.ChoiceField(
        choices=[("DD MMM YYYY", "13 Aug 2026"), ("MM/DD/YYYY", "08/13/2026"), ("YYYY-MM-DD", "2026-08-13")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    time_format = forms.ChoiceField(
        choices=[("12", "12-hour"), ("24", "24-hour")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )

    text_size = forms.ChoiceField(
        choices=[("small", "Small"), ("default", "Default"), ("large", "Large")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    high_contrast = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))
    reduced_motion = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))

    profile_visibility = forms.ChoiceField(
        choices=[("public", "Everyone"), ("members", "Shared community members"), ("private", "Only me")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    message_permission = forms.ChoiceField(
        choices=[("everyone", "Everyone"), ("members", "Shared community members"), ("nobody", "Nobody")],
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    show_online_status = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))
    show_activity = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))
    search_visibility = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}))

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        if profile is not None and not self.is_bound:
            initial = profile.personalization
            initial["quiet_hours_start"] = initial.get("quiet_hours_start", "21:00")
            initial["quiet_hours_end"] = initial.get("quiet_hours_end", "08:00")
            self.initial.update(initial)

    def save(self):
        if self.profile is None:
            raise ValueError("A profile is required to save personalization preferences.")
        existing = self.profile.personalization_preferences
        preferences = dict(existing) if isinstance(existing, dict) else {}
        preferences.update(self.cleaned_data.copy())
        preferences["quiet_hours_start"] = preferences["quiet_hours_start"].strftime("%H:%M")
        preferences["quiet_hours_end"] = preferences["quiet_hours_end"].strftime("%H:%M")
        self.profile.personalization_preferences = preferences
        self.profile.theme_preference = preferences["theme_preference"]
        self.profile.save(update_fields=["personalization_preferences", "theme_preference"])
        return self.profile


class ProfileQuickEditForm(forms.ModelForm):
    MAX_PROFILE_IMAGE_SIZE = 2 * 1024 * 1024

    username = forms.CharField(max_length=150, required=False, widget=forms.HiddenInput())
    first_name = forms.CharField(max_length=150, required=False, widget=forms.HiddenInput())
    email = forms.EmailField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = UserProfile
        fields = ["profile_image"]
        widgets = {
            "profile_image": forms.FileInput(attrs={"accept": "image/*"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_profile_image(self):
        image = self.cleaned_data.get("profile_image")
        if not image:
            return image
        content_type = getattr(image, "content_type", "")
        if content_type and not content_type.startswith("image/"):
            raise forms.ValidationError("Please upload an image file.")
        if image.size > self.MAX_PROFILE_IMAGE_SIZE:
            raise forms.ValidationError("Profile image must be 2 MB or smaller.")
        return image

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            return username
        User = get_user_model()
        queryset = User.objects.filter(username__iexact=username)
        if self.user is not None:
            queryset = queryset.exclude(pk=self.user.pk)
        if queryset.exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if not email:
            return email
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("This email is already used by another account.")
        return email

    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.user is not None:
            username = self.cleaned_data.get("username", "").strip()
            first_name = self.cleaned_data.get("first_name", "").strip()
            email = self.cleaned_data.get("email", "").strip().lower()
            if username:
                self.user.username = username
            if first_name:
                self.user.first_name = first_name
            if email:
                self.user.email = email
            if commit:
                update_fields = []
                if username:
                    update_fields.append("username")
                if first_name:
                    update_fields.append("first_name")
                if email:
                    update_fields.append("email")
                if update_fields:
                    self.user.save(update_fields=update_fields)
        if commit:
            profile.save()
        return profile


class KYCVerificationForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["kyc_full_name", "kyc_national_id", "kyc_date_of_birth"]
        widgets = {
            "kyc_full_name": forms.TextInput(
                attrs={"placeholder": "Full name as per ID", "class": FIELD_CLASS}
            ),
            "kyc_national_id": forms.TextInput(
                attrs={"placeholder": "National ID / Passport number", "class": FIELD_CLASS}
            ),
            "kyc_date_of_birth": forms.DateInput(
                attrs={"type": "date", "class": FIELD_CLASS}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_kyc_full_name(self):
        name = self.cleaned_data.get("kyc_full_name", "").strip()
        if not name:
            raise forms.ValidationError("Full name is required.")
        if len(name) < 3:
            raise forms.ValidationError("Full name must be at least 3 characters long.")
        return name

    def clean_kyc_national_id(self):
        national_id = self.cleaned_data.get("kyc_national_id", "").strip()
        if not national_id:
            raise forms.ValidationError("National ID / Passport number is required.")
        if len(national_id) < 5:
            raise forms.ValidationError("ID must be at least 5 characters long.")
        return national_id

    def clean_kyc_date_of_birth(self):
        dob = self.cleaned_data.get("kyc_date_of_birth")
        if not dob:
            raise forms.ValidationError("Date of birth is required.")
        from datetime import date, timedelta
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 18:
            raise forms.ValidationError("You must be at least 18 years old.")
        return dob

    def save(self, commit=True):
        profile = super().save(commit=False)
        if commit:
            profile.kyc_verified = True
            profile.kyc_verified_at = timezone.now()
            profile.save()
        return profile


class ContentReportForm(forms.ModelForm):
    class Meta:
        model = ContentReport
        fields = ["reason", "description"]
        widgets = {
            "reason": forms.Select(attrs={"class": FIELD_CLASS}),
            "description": forms.Textarea(
                attrs={
                    "placeholder": "What happened?",
                    "rows": 3,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            ),
        }


class PostCommentForm(forms.ModelForm):
    class Meta:
        model = PostComment
        fields = ["body", "attachment_file", "attachment_url"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "placeholder": "Write a comment",
                    "rows": 2,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            ),
            "attachment_file": forms.FileInput(
                attrs={"accept": "image/*,video/*", "class": "hidden"}
            ),
            "attachment_url": forms.URLInput(
                attrs={"placeholder": "Attachment URL (optional)", "class": FIELD_CLASS},
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        body = cleaned_data.get("body", "") or ""
        attachment_file = cleaned_data.get("attachment_file")
        attachment_url = cleaned_data.get("attachment_url", "") or ""
        if not body.strip() and not attachment_file and not attachment_url.strip():
            raise forms.ValidationError("Write a reply or attach a file before submitting.")
        return cleaned_data


class SubscriptionPlanForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = ["name", "slug", "price", "community_limit", "description", "feature_summary", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Plan name", "class": FIELD_CLASS}),
            "slug": forms.TextInput(attrs={"placeholder": "basic, standard, premium", "class": FIELD_CLASS}),
            "price": forms.NumberInput(attrs={"placeholder": "Price", "class": FIELD_CLASS}),
            "community_limit": forms.NumberInput(attrs={"placeholder": "Community limit", "class": FIELD_CLASS}),
            "description": forms.Textarea(attrs={"placeholder": "Description", "rows": 3, "class": f"{FIELD_CLASS} resize-none"}),
            "feature_summary": forms.Textarea(attrs={"placeholder": "Plan features", "rows": 3, "class": f"{FIELD_CLASS} resize-none"}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
        }


class PaymentSettingsForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["bank_name", "bank_account_holder", "bank_account_number", "esewa_id", "khalti_id"]
        widgets = {
            "bank_name": forms.TextInput(attrs={"placeholder": "e.g. Nepal Bank Limited", "class": FIELD_CLASS}),
            "bank_account_holder": forms.TextInput(attrs={"placeholder": "Full name on bank account", "class": FIELD_CLASS}),
            "bank_account_number": forms.TextInput(attrs={"placeholder": "Bank account number", "class": FIELD_CLASS}),
            "esewa_id": forms.TextInput(attrs={"placeholder": "eSewa ID / phone number", "class": FIELD_CLASS}),
            "khalti_id": forms.TextInput(attrs={"placeholder": "Khalti ID / phone number", "class": FIELD_CLASS}),
        }


class PayoutRequestForm(forms.Form):
    amount = forms.DecimalField(
        min_value=Decimal("1000.00"),
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "min": "1000",
                "step": "0.01",
                "placeholder": "Minimum Rs. 1,000",
                "class": FIELD_CLASS,
            }
        ),
        label="Payout amount",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": "Optional note for the payout reviewer",
                "class": f"{FIELD_CLASS} resize-none",
            }
        ),
        label="Note",
    )


class ContactMessageForm(forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ["full_name", "email", "message"]
        widgets = {
            "full_name": forms.TextInput(
                attrs={
                    "placeholder": "Full name",
                    "class": (
                        "w-full px-5 py-3.5 rounded-xl border border-gray-200 bg-white "
                        "focus:ring-1 focus:ring-indigo-300 outline-none text-sm"
                    ),
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "Email address",
                    "class": (
                        "w-full px-5 py-3.5 rounded-xl border border-gray-200 bg-white "
                        "focus:ring-1 focus:ring-indigo-300 outline-none text-sm"
                    ),
                }
            ),
            "message": forms.Textarea(
                attrs={
                    "placeholder": "Tell us about your project...",
                    "rows": 3,
                    "class": (
                        "w-full px-5 py-3.5 rounded-xl border border-gray-200 bg-white "
                        "focus:ring-1 focus:ring-indigo-300 outline-none text-sm resize-none"
                    ),
                }
            ),
        }


INPUT_CLASS = "w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition focus:border-purple-400 focus:ring-4 focus:ring-purple-100"


class CommunityRoomForm(forms.ModelForm):
    class Meta:
        model = CommunityRoom
        fields = ["name", "room_type", "layout", "description", "icon_class", "icon_image_url", "icon_type", "color", "display_order"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Room name", "class": INPUT_CLASS}),
            "room_type": forms.HiddenInput(),
            "layout": forms.HiddenInput(),
            "description": forms.Textarea(
                attrs={"placeholder": "Optional description", "rows": 2, "class": INPUT_CLASS + " resize-none"}
            ),
            "icon_class": forms.TextInput(
                attrs={"placeholder": "e.g. fa-solid fa-bullhorn", "class": INPUT_CLASS}
            ),
            "icon_image_url": forms.URLInput(
                attrs={"placeholder": "https://...", "class": INPUT_CLASS}
            ),
            "icon_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "color": forms.TextInput(
                attrs={"placeholder": "e.g. purple, blue, green", "class": INPUT_CLASS}
            ),
            "display_order": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": "0"}),
        }


class CommunityLinkForm(forms.ModelForm):
    class Meta:
        model = CommunityLink
        fields = ["title", "url"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Link title", "class": INPUT_CLASS}),
            "url": forms.URLInput(attrs={"placeholder": "https://...", "class": INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
