from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from .models import (
    Community,
    CommunityMembership,
    CommunityPost,
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
    class Meta:
        model = Community
        fields = ["name", "topic", "visibility", "description", "rules", "allow_member_posts"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Community name", "class": FIELD_CLASS}),
            "topic": forms.TextInput(attrs={"placeholder": "Topic", "class": FIELD_CLASS}),
            "visibility": forms.Select(attrs={"class": FIELD_CLASS}),
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
            "allow_member_posts": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
        }


class AdminUserCreationForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={"placeholder": "Temporary password", "class": FIELD_CLASS}
        )
    )
    is_staff = forms.BooleanField(required=False)

    class Meta:
        model = get_user_model()
        fields = ["first_name", "username", "email", "password", "is_staff", "is_active"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "Full name", "class": FIELD_CLASS}),
            "username": forms.TextInput(attrs={"placeholder": "Username", "class": FIELD_CLASS}),
            "email": forms.EmailInput(attrs={"placeholder": "Email address", "class": FIELD_CLASS}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
            "is_staff": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300 text-purple-700"}),
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
        if commit:
            user.save()
        return user


class AdminCommunityForm(CommunityForm):
    class Meta(CommunityForm.Meta):
        fields = ["name", "topic", "visibility", "owner", "description", "rules", "allow_member_posts"]
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
        fields = ["community", "title", "category", "content_type", "body", "attachment_url"]
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
            "attachment_url": forms.URLInput(attrs={"placeholder": "Attachment URL (optional)", "class": FIELD_CLASS}),
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
        fields = ["community", "author_user", "title", "category", "content_type", "body", "attachment_url", "is_pinned"]
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
        fields = ["first_name", "email", "phone_number", "address", "bio", "interests", "profile_image", "theme_preference"]
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
            self.fields["first_name"].initial = user.first_name
            self.fields["email"].initial = user.email

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
            self.user.first_name = self.cleaned_data.get("first_name", "").strip()
            self.user.email = self.cleaned_data.get("email", "").strip().lower()
            if commit:
                self.user.save(update_fields=["first_name", "email"])
        if commit:
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
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "placeholder": "Write a comment",
                    "rows": 2,
                    "class": f"{FIELD_CLASS} resize-none",
                }
            )
        }


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
