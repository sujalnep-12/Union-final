import json
import logging
import random
import smtplib
from datetime import timedelta
from uuid import uuid4
from urllib.parse import quote, urlencode, urlparse
from urllib.request import urlopen

from django.contrib import messages

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db.models import Count, Exists, IntegerField, OuterRef, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .badges import check_and_award
from .esewa import (
    EsewaConfigurationError,
    EsewaStatusCheckError,
    EsewaVerificationError,
    build_payment_data,
    decode_response,
    is_configured as esewa_is_configured,
    status_check as esewa_status_check,
    verify_response_signature,
)
from .forms import (
    AdminCommunityForm,
    AdminCommunityPostForm,
    AdminUserCreationForm,
    CommunityCreateForm,
    CommunityForm,
    CommunityLinkForm,
    CommunityPostForm,
    CommunityRoomForm,
    ContactMessageForm,
    ContentReportForm,
    KYCVerificationForm,
    PostCommentForm,
    PersonalizationForm,
    PayoutRequestForm,
    SubscriptionPlanForm,
    UserProfileForm,
    PaymentSettingsForm,
)
from decimal import Decimal

from .models import (
    Announcement,
    AuditLog,
    Community,
    CommunityEvent,
    CommunityLink,
    CommunityMembership,
    CommunityPost,
    CommunityRoom,
    ContactMessage,
    CommentEngagement,
    ContentReport,
    DirectMessage,
    DirectMessageThread,
    Notification,
    PaymentTransaction,
    PayoutRequest,
    PostComment,
    PostDonation,
    PostEngagement,
    HiddenPost,
    POST_ATTACHMENT_ALLOWED_EXTENSIONS,
    POST_ATTACHMENT_MAX_SIZE,
    SubscriptionPlan,
    UserProfile,
    UserSubscription,
    BookmarkedPost, UserFollow, UserReputation, PostPoll, PollOption, PollVote, PostAward, Badge, UserBadge,
)


REMEMBER_ME_SESSION_AGE = 60 * 60 * 24 * 14
logger = logging.getLogger(__name__)

NON_DELIVERY_EMAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
}


def _is_ajax_request(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _ajax_error_response(request, message, status=400):
    messages.error(request, message)
    if _is_ajax_request(request):
        return HttpResponse(message, status=status)
    return None


def _community_role_for(user, community):
    """Return the user's effective role in a community.

    The owner foreign key remains the source of truth for ownership, while
    the membership row carries the portal role used for day-to-day access.
    """
    if community.owner_id == user.id:
        return CommunityMembership.OWNER
    membership = CommunityMembership.objects.filter(
        community=community,
        user=user,
        status=CommunityMembership.ACTIVE,
    ).first()
    return membership.role if membership else None


def _community_has_permission(user, community, permission):
    role = _community_role_for(user, community)
    return bool(role and permission in CommunityMembership.ROLE_PERMISSIONS.get(role, set()))


def _platform_role(user):
    """Return the effective platform role for global administration."""
    if not getattr(user, "is_authenticated", False):
        return UserProfile.PLATFORM_MEMBER
    # Existing Django superusers are the platform owner during the transition
    # to the explicit platform-role field.
    if user.is_superuser:
        return UserProfile.PLATFORM_OWNER
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return UserProfile.PLATFORM_MEMBER
    return profile.platform_role


def _is_platform_admin(user):
    return _platform_role(user) in {
        UserProfile.PLATFORM_OWNER,
        UserProfile.PLATFORM_ADMIN,
    }


def _is_platform_owner(user):
    return _platform_role(user) == UserProfile.PLATFORM_OWNER


def _manageable_platform_roles(user):
    """Return the platform roles the current user may assign to another user."""
    if _is_platform_owner(user):
        return {choice[0] for choice in UserProfile.PLATFORM_ROLE_CHOICES}
    if _platform_role(user) == UserProfile.PLATFORM_ADMIN:
        # An admin has platform-management access, but cannot create or promote
        # an account to the financially privileged Owner role.
        return {UserProfile.PLATFORM_ADMIN, UserProfile.PLATFORM_MEMBER}
    return set()


def _infer_link_icon_metadata(url):
    parsed = urlparse(url or "")
    hostname = (parsed.hostname or parsed.netloc or "").strip().lower()
    hostname = hostname.replace("www.", "")
    if not hostname:
        return "fa-solid fa-globe", ""

    if hostname.startswith("github.com"):
        icon_class = "fa-brands fa-github"
    elif hostname.startswith("linkedin.com"):
        icon_class = "fa-brands fa-linkedin"
    elif hostname.startswith("youtube.com") or hostname.startswith("youtu.be"):
        icon_class = "fa-brands fa-youtube"
    elif hostname.startswith("x.com") or hostname.startswith("twitter.com"):
        icon_class = "fa-brands fa-x-twitter"
    else:
        icon_class = "fa-solid fa-globe"

    icon_image_url = f"https://www.google.com/s2/favicons?domain={hostname}&sz=64"
    return icon_class, icon_image_url


def _post_attachment_error(attachment_file):
    if not attachment_file:
        return ""
    if getattr(attachment_file, "size", 0) > POST_ATTACHMENT_MAX_SIZE:
        return "Attachment must be 50 MB or smaller."
    filename = getattr(attachment_file, "name", "")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not extension or extension not in POST_ATTACHMENT_ALLOWED_EXTENSIONS:
        return "Use a JPG, PNG, GIF, WebP, MP4, WebM, MOV, or M4V attachment."
    return ""


def _safe_next_url(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not next_url:
        return ""

    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return ""


USER_DASHBOARD_SECTION_ORDER = [
    ("overview", "Home", "fa-house"),
    ("discover", "Discover", "fa-compass"),
    ("communities", "Communities", "fa-layer-group"),
    ("messages", "Messages", "fa-message"),
    ("notifications", "Notifications", "fa-bell"),
    ("subscriptions", "Subscriptions", "facredit-card"),
    ("payments", "Payments", "fa-wallet"),
    ("studio", "Studio", "fa-palette"),
    ("reports", "Reports", "fa-chart-simple"),
    ("profile", "Profile", "fa-user"),
    ("personalization", "Personalization", "fa-sliders"),
    ("settings", "Settings", "fa-gear"),
]
USER_DASHBOARD_SECTIONS = {key for key, _, _ in USER_DASHBOARD_SECTION_ORDER}

SUPER_ADMIN_SECTION_ORDER = [
    ("overview", "Overview", "fa-table-cells-large"),
    ("users", "Users", "fa-users-gear"),
    ("communities", "Communities", "fa-layer-group"),
    ("messages", "Messages", "fa-envelope"),
    ("moderation", "Policy", "fa-shield-halved"),
    ("subscriptions", "Subscriptions", "fa-credit-card"),
    ("system", "System", "fa-server"),
]
SUPER_ADMIN_SECTION_KEYS = {key for key, _, _ in SUPER_ADMIN_SECTION_ORDER}

COMMUNITY_DASHBOARD_TABS = {"discover", "joined", "managed"}

COMMUNITY_CARD_COVERS = [
    "linear-gradient(135deg, rgba(17, 24, 39, 0.18), rgba(17, 24, 39, 0.82)), linear-gradient(135deg, #c084fc, #111827)",
    "linear-gradient(135deg, rgba(17, 24, 39, 0.14), rgba(17, 24, 39, 0.78)), linear-gradient(135deg, #38bdf8, #4338ca)",
    "linear-gradient(135deg, rgba(17, 24, 39, 0.12), rgba(17, 24, 39, 0.82)), linear-gradient(135deg, #22c55e, #0f172a)",
    "linear-gradient(135deg, rgba(17, 24, 39, 0.08), rgba(17, 24, 39, 0.82)), linear-gradient(135deg, #f97316, #312e81)",
    "linear-gradient(135deg, rgba(17, 24, 39, 0.1), rgba(17, 24, 39, 0.84)), linear-gradient(135deg, #14b8a6, #581c87)",
    "linear-gradient(135deg, rgba(17, 24, 39, 0.12), rgba(17, 24, 39, 0.86)), linear-gradient(135deg, #ef4444, #0f172a)",
]


def _dashboard_next_url():
    return reverse("hub:dashboard")


def _dashboard_section_url(section):
    if section == "overview":
        return reverse("hub:dashboard")
    return reverse("hub:dashboard_section", kwargs={"section": section})


PAYOUT_MINIMUM = Decimal("1000.00")


def _community_payout_summary(community):
    """Return lifetime earnings and the amount currently available to withdraw."""
    membership_total = (
        PaymentTransaction.objects.filter(
            community=community,
            status=PaymentTransaction.SUCCESS,
        ).exclude(reference_code__startswith="DON-").aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    donation_total = (
        PostDonation.objects.filter(
            post__community=community,
            status=PostDonation.SUCCESS,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    lifetime_total = membership_total + donation_total
    paid_total = (
        PayoutRequest.objects.filter(
            community=community,
            status=PayoutRequest.PAID,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    held_total = (
        PayoutRequest.objects.filter(
            community=community,
            status__in=[PayoutRequest.PENDING, PayoutRequest.PROCESSING],
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    available_total = max(lifetime_total - paid_total - held_total, Decimal("0.00"))
    return {
        "lifetime_total": lifetime_total,
        "paid_total": paid_total,
        "held_total": held_total,
        "available_total": available_total,
        "minimum": PAYOUT_MINIMUM,
        "can_request": available_total >= PAYOUT_MINIMUM,
    }


def _payout_method_for_profile(profile):
    if profile.bank_account_number:
        return PayoutRequest.BANK
    if profile.esewa_id:
        return PayoutRequest.ESEWA
    if profile.khalti_id:
        return PayoutRequest.KHALTI
    return ""


def _creator_payout_summary(user, community_ids):
    """Return one payout balance across every community owned by the creator."""
    community_ids = list(community_ids)
    membership_total = (
        PaymentTransaction.objects.filter(
            community_id__in=community_ids,
            status=PaymentTransaction.SUCCESS,
        ).exclude(reference_code__startswith="DON-").aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    donation_total = (
        PostDonation.objects.filter(
            post__community_id__in=community_ids,
            status=PostDonation.SUCCESS,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    payout_scope = PayoutRequest.objects.filter(requested_by=user).filter(
        Q(community_id__in=community_ids) | Q(community__isnull=True)
    )
    paid_total = (
        payout_scope.filter(status=PayoutRequest.PAID).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    held_total = (
        payout_scope.filter(status__in=[PayoutRequest.PENDING, PayoutRequest.PROCESSING]).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    lifetime_total = membership_total + donation_total
    available_total = max(lifetime_total - paid_total - held_total, Decimal("0.00"))
    return {
        "lifetime_total": lifetime_total,
        "membership_total": membership_total,
        "donation_total": donation_total,
        "paid_total": paid_total,
        "held_total": held_total,
        "available_total": available_total,
        "minimum": PAYOUT_MINIMUM,
        "can_request": available_total >= PAYOUT_MINIMUM,
    }


def _dashboard_onboarding_started(profile, section):
    """Return whether a user has dismissed a first-visit dashboard intro."""
    preferences = profile.personalization_preferences
    if not isinstance(preferences, dict):
        return False
    onboarding = preferences.get("dashboard_onboarding", {})
    return isinstance(onboarding, dict) and bool(onboarding.get(section))


def _mark_dashboard_onboarding_started(profile, section):
    """Persist a dashboard intro dismissal without replacing other preferences."""
    preferences = dict(profile.personalization_preferences or {})
    onboarding = preferences.get("dashboard_onboarding", {})
    onboarding = dict(onboarding) if isinstance(onboarding, dict) else {}
    if onboarding.get(section):
        return
    onboarding[section] = True
    preferences["dashboard_onboarding"] = onboarding
    profile.personalization_preferences = preferences
    profile.save(update_fields=["personalization_preferences"])


def _notifications_for_user(user):
    return Notification.objects.filter(user=user).select_related("actor", "community", "room").order_by("-created_at")


def _unread_notification_count(user):
    return Notification.objects.filter(user=user, is_read=False, is_archived=False).count()


def _user_dashboard_sections():
    return [
        {
            "key": key,
            "label": label,
            "icon": icon,
            "url": reverse("hub:dashboard") if key == "overview" else reverse("hub:dashboard_section", kwargs={"section": key}),
        }
        for key, label, icon in USER_DASHBOARD_SECTION_ORDER
    ]


def _remember_me_enabled(request):
    return request.POST.get("remember_me") == "on"


def _start_authenticated_session(request, user, *, remember_me=False, entrypoint="signin"):
    tracked_ids = []
    for value in request.session.get("account_switch_user_ids", []):
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value not in tracked_ids:
            tracked_ids.append(value)
    if getattr(request.user, "is_authenticated", False) and request.user.pk not in tracked_ids:
        tracked_ids.append(request.user.pk)

    login(request, user)
    if remember_me:
        request.session.set_expiry(REMEMBER_ME_SESSION_AGE)
    else:
        request.session.set_expiry(0)
    request.session["auth_entrypoint"] = entrypoint
    request.session["remember_me"] = remember_me
    request.session["authenticated_at"] = timezone.now().isoformat()
    if user.pk not in tracked_ids:
        tracked_ids.append(user.pk)
    request.session["account_switch_user_ids"] = tracked_ids[-5:]


def _session_context(request):
    expires_at = None
    if request.session.get_expire_at_browser_close():
        expiry_label = "When the browser closes"
    else:
        expires_at = request.session.get_expiry_date()
        expiry_label = expires_at.strftime("%b %d, %Y %I:%M %p")

    return {
        "entrypoint": request.session.get("auth_entrypoint", "signin"),
        "remember_me": request.session.get("remember_me", False),
        "authenticated_at": request.session.get("authenticated_at", ""),
        "expires_at": expires_at,
        "expiry_label": expiry_label,
    }


def _display_name(user):
    return user.first_name or user.get_username().split("@")[0] or "Member"


def _signup_context(**extra):
    context = {
        "gender_choices": UserProfile.GENDER_CHOICES,
        "google_client_id": settings.GOOGLE_CLIENT_ID,
    }
    context.update(extra)
    return context


def _generate_otp():
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def _generate_ai_summary(body):
    if not body or len(body.strip()) < 20:
        return body[:180] if body else ""
    prompt = "Summarize this post in 2-3 sentences:\n\n" + body
    payload = json.dumps({
        "model": "llama3.1:8b", "prompt": prompt,
        "stream": False, "options": {"num_predict": 100}
    }).encode()
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload, headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            summary = json.loads(resp.read()).get("response", "").strip()
            if summary:
                return summary
    except Exception:
        pass
    return body[:180]


def _email_backend_sends_real_email():
    return settings.EMAIL_BACKEND not in NON_DELIVERY_EMAIL_BACKENDS


def _store_debug_otp(request, otp_code, *, email_sent):
    if settings.DEBUG and (not email_sent or not _email_backend_sends_real_email()):
        request.session["debug_otp_code"] = otp_code
    else:
        request.session.pop("debug_otp_code", None)


def _otp_context(request, user, next_url, reset_flow=False):
    return {
        "email": user.email,
        "next": next_url,
        "debug_otp_code": request.session.get("debug_otp_code") if settings.DEBUG else "",
        "reset_flow": reset_flow,
    }


def _send_verification_otp(user, *, reset_flow=False):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    otp_code = _generate_otp()
    profile.set_otp(otp_code)
    profile.save(update_fields=["otp_code", "otp_created_at"])

    subject = "Your Union verification code"
    if reset_flow:
        body = (
            f"Hi {_display_name(user)},\n\n"
            f"Your Union password reset code is {otp_code}.\n\n"
            "Enter this code to reset your password."
        )
    else:
        body = (
            f"Hi {_display_name(user)},\n\n"
            f"Your Union verification code is {otp_code}.\n\n"
            "Enter this code to finish creating your account. "
            "After verification, log in with your username and password."
        )

    try:
        sent_count = send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send verification OTP to %s", user.email)
        return False, otp_code

    return sent_count > 0, otp_code


def _add_otp_delivery_message(request, *, email_sent, created=False, reset_flow=False):
    if email_sent and _email_backend_sends_real_email():
        detail = "Account created. " if created else ""
        if reset_flow:
            messages.success(request, f"Check your email for the password reset OTP code.")
        else:
            messages.success(request, f"{detail}Check your email for the OTP code.")
        return

    if email_sent:
        if reset_flow:
            messages.success(
                request,
                "Development email mode is active, so use the password reset OTP code shown below.",
            )
        else:
            detail = "Account created. " if created else ""
            messages.success(
                request,
                f"{detail}Development email mode is active, so use the OTP code shown below.",
            )
        return

    messages.error(
        request,
        "We could not send the OTP email. Check your SMTP settings, then use Resend OTP.",
    )


def _verify_google_id_token(id_token):
    if not settings.GOOGLE_CLIENT_ID:
        return None

    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={quote(id_token)}"
        with urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        return None

    if payload.get("aud") != settings.GOOGLE_CLIENT_ID:
        return None

    if payload.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        return None

    if str(payload.get("email_verified", "")).lower() not in {"true", "1"}:
        return None

    return payload


def _create_or_get_google_user(payload):
    email = payload.get("email", "").strip().lower()
    if not email:
        return None

    User = get_user_model()
    user = User.objects.filter(email__iexact=email).first()

    if user is None:
        username_base = email.split("@")[0]
        candidate = username_base
        suffix = 1
        while User.objects.filter(username__iexact=candidate).exists():
            candidate = f"{username_base}{suffix}"
            suffix += 1

        user = User(
            username=candidate,
            email=email,
            first_name=payload.get("given_name", "")[:150],
            last_name=payload.get("family_name", "")[:150],
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        UserProfile.objects.create(user=user, email_verified=True)
    else:
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.email_verified:
            profile.email_verified = True
            profile.save(update_fields=["email_verified"])

    return user


@login_required
def toggle_follow(request, username):
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    if target == request.user:
        messages.error(request, "You cannot follow yourself.")
        return redirect(request.META.get("HTTP_REFERER", "hub:dashboard"))
    follow, created = UserFollow.objects.get_or_create(follower=request.user, followed=target)
    if not created:
        follow.delete()
        messages.success(request, f"Unfollowed @{target.username}.")
    else:
        messages.success(request, f"Following @{target.username}.")
        check_and_award(target)
    return redirect(request.META.get("HTTP_REFERER", "hub:dashboard"))


def google_signin(request):
    if request.method != "POST":
        return redirect("hub:signin")

    id_token = request.POST.get("id_token", "").strip()
    next_url = _safe_next_url(request) or _dashboard_next_url()

    if not id_token:
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect("hub:signin")

    payload = _verify_google_id_token(id_token)
    if payload is None:
        messages.error(request, "Unable to verify Google sign-in. Please try again.")
        return redirect("hub:signin")

    user = _create_or_get_google_user(payload)
    if user is None:
        messages.error(request, "Google sign-in did not return a valid email.")
        return redirect("hub:signin")

    _start_authenticated_session(request, user, remember_me=True, entrypoint="google")
    return redirect(next_url)


def _community_cards(queryset):
    return queryset.annotate(
        member_count=Count("memberships", distinct=True),
        post_count=Count("posts", distinct=True),
    )


def _community_tab_from_request(request):
    tab = request.GET.get("tab", "discover")
    if tab not in COMMUNITY_DASHBOARD_TABS:
        return "discover"
    return tab


def _community_listing_for_user(user, *, tab="discover", search="", category="", sort="popular"):
    queryset = _community_cards(Community.objects.select_related("owner").filter(is_archived=False))

    if tab == "joined":
        queryset = queryset.filter(memberships__user=user)
    elif tab == "managed":
        queryset = queryset.filter(owner=user)
    else:
        queryset = queryset.filter(Q(visibility=Community.PUBLIC) | Q(memberships__user=user))

    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(topic__icontains=search)
            | Q(location__icontains=search)
            | Q(tags__icontains=search)
            | Q(interests__icontains=search)
            | Q(description__icontains=search)
        )

    if category:
        queryset = queryset.filter(topic__iexact=category)

    if sort == "recent":
        ordering = ("-created_at", "name")
    elif sort == "name":
        ordering = ("name",)
    else:
        ordering = ("-member_count", "-post_count", "name")

    communities = list(queryset.distinct().order_by(*ordering))
    joined_ids = set(
        CommunityMembership.objects.filter(user=user, community__in=communities).values_list("community_id", flat=True)
    )
    for index, community in enumerate(communities):
        community.is_owner = community.owner_id == user.id
        community.is_member = community.is_owner or community.pk in joined_ids
        community.cover_style = COMMUNITY_CARD_COVERS[index % len(COMMUNITY_CARD_COVERS)]
    return communities


def _visible_communities_for(user):
    return (
        Community.objects.select_related("owner")
        .filter(is_archived=False)
        .filter(Q(visibility=Community.PUBLIC) | Q(memberships__user=user) | Q(owner=user))
        .distinct()
    )


def _direct_message_thread_for(user, other_user):
    participant1, participant2 = sorted([user, other_user], key=lambda participant: participant.pk)
    return DirectMessageThread.objects.get_or_create(
        participant1=participant1,
        participant2=participant2,
    )


def _message_user_results(user, search):
    search = search.strip()
    if not search:
        return get_user_model().objects.none()

    return (
        get_user_model()
        .objects.filter(is_active=True)
        .exclude(pk=user.pk)
        .filter(
            Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )
        .order_by("username")[:10]
    )


def home(request):
    contact_form = ContactMessageForm()

    if request.method == "POST":
        contact_form = ContactMessageForm(request.POST)
        if contact_form.is_valid():
            contact_form.save()
            messages.success(request, "Thanks for reaching out. We will get back to you soon.")
            return HttpResponseRedirect(f"{reverse('hub:home')}#contact")
        messages.error(request, "Please correct the contact form and try again.")

    return render(request, "landing.html", {"contact_form": contact_form})


def get_started(request):
    if request.user.is_authenticated:
        return redirect("hub:dashboard")
    signup_url = f"{reverse('hub:signup')}?next={_dashboard_next_url()}"
    return redirect(signup_url)


def signup(request):
    adding_account = request.GET.get("add_account") == "1" or request.POST.get("add_account") == "1"
    if request.user.is_authenticated and not adding_account:
        return redirect(_safe_next_url(request) or "hub:dashboard")

    if adding_account:
        request.session["account_add_flow"] = True

    next_url = _safe_next_url(request) or _dashboard_next_url()

    if request.method == "POST":
        full_name = request.POST.get("full_name", "").strip()
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip().lower()
        date_of_birth = request.POST.get("date_of_birth", "").strip()
        gender = request.POST.get("gender", UserProfile.PREFER_NOT_TO_SAY)
        phone_number = request.POST.get("phone_number", "").strip()
        password = request.POST.get("password", "")
        form_data = {
            "full_name": full_name,
            "username": username,
            "email": email,
            "date_of_birth": date_of_birth,
            "gender": gender,
            "phone_number": phone_number,
        }
        User = get_user_model()

        if not full_name or not username or not email or not date_of_birth or not gender or not phone_number or not password:
            messages.error(request, "Please fill in all required fields.")
            return render(request, "signup.html", _signup_context(form_data=form_data, next=next_url))

        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "Please enter a valid email address.")
            return render(request, "signup.html", _signup_context(form_data=form_data, next=next_url))

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, "This username is already taken.")
            return render(request, "signup.html", _signup_context(form_data=form_data, next=next_url))

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "An account with this email already exists. Please login instead.")
            return render(request, "signup.html", _signup_context(form_data=form_data, next=next_url))

        if gender not in dict(UserProfile.GENDER_CHOICES):
            messages.error(request, "Please select a valid gender option.")
            return render(request, "signup.html", _signup_context(form_data=form_data, next=next_url))

        parsed_date_of_birth = parse_date(date_of_birth)
        if parsed_date_of_birth is None:
            messages.error(request, "Please enter a valid date of birth.")
            return render(request, "signup.html", _signup_context(form_data=form_data, next=next_url))

        user = User(username=username, email=email, first_name=full_name[:150], is_active=False)

        try:
            validate_password(password, user)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
            return render(request, "signup.html", {"form_data": form_data, "next": next_url})

        user.set_password(password)
        user.save()
        UserProfile.objects.create(
            user=user,
            date_of_birth=parsed_date_of_birth,
            gender=gender,
            phone_number=phone_number,
        )
        email_sent, otp_code = _send_verification_otp(user)
        request.session["pending_verification_user_id"] = user.pk
        request.session["pending_verification_next"] = next_url
        _store_debug_otp(request, otp_code, email_sent=email_sent)
        _add_otp_delivery_message(request, email_sent=email_sent, created=True)
        return redirect("hub:verify_otp")

    return render(
        request,
        "signup.html",
        _signup_context(
            next=next_url,
            form_data={"email": request.GET.get("email", "")},
        ),
    )


def verify_otp(request):
    User = get_user_model()
    pending_verification_user_id = request.session.get("pending_verification_user_id")
    pending_password_reset_user_id = request.session.get("pending_password_reset_user_id")
    reset_flow = False
    user = None

    if pending_verification_user_id:
        user = User.objects.filter(pk=pending_verification_user_id, is_active=False).first()
    elif pending_password_reset_user_id:
        user = User.objects.filter(pk=pending_password_reset_user_id).first()
        reset_flow = True

    if user is None:
        messages.error(request, "No pending OTP verification was found.")
        return redirect("hub:signup") if not reset_flow else redirect("hub:forgot_password")

    next_url = request.session.get("pending_verification_next") if not reset_flow else None
    if not next_url:
        next_url = request.session.get("pending_password_reset_next") or _dashboard_next_url()

    if request.method == "POST":
        action = request.POST.get("action", "verify")
        profile = get_object_or_404(UserProfile, user=user)

        if action == "resend":
            email_sent, otp_code = _send_verification_otp(user, reset_flow=reset_flow)
            _store_debug_otp(request, otp_code, email_sent=email_sent)
            _add_otp_delivery_message(request, email_sent=email_sent, reset_flow=reset_flow)
            return redirect("hub:verify_otp")

        otp_code = request.POST.get("otp_code", "").strip()
        if otp_code != profile.otp_code:
            messages.error(request, "Invalid OTP code. Please check your email and try again.")
            return render(request, "verify_otp.html", _otp_context(request, user, next_url, reset_flow=reset_flow))

        profile.otp_code = ""
        profile.save(update_fields=["otp_code"])

        if reset_flow:
            request.session["pending_password_reset_verified"] = True
            return redirect("hub:reset_password")

        user.is_active = True
        user.save(update_fields=["is_active"])
        profile.email_verified = True
        profile.save(update_fields=["email_verified"])
        request.session.pop("pending_verification_user_id", None)
        request.session.pop("pending_verification_next", None)
        request.session.pop("debug_otp_code", None)
        messages.success(request, "Email verified. Please log in with your username and password.")
        account_add_flow = request.session.pop("account_add_flow", False)
        if account_add_flow:
            signin_params = {"next": next_url, "switch_account": "1"}
            return redirect(f"{reverse('hub:signin')}?{urlencode(signin_params)}")
        return redirect(f"{reverse('hub:signin')}?next={next_url}")

    return render(request, "verify_otp.html", _otp_context(request, user, next_url, reset_flow=reset_flow))


def reset_password(request):
    User = get_user_model()
    user_id = request.session.get("pending_password_reset_user_id")
    verified = request.session.get("pending_password_reset_verified")
    user = User.objects.filter(pk=user_id).first() if user_id else None

    if user is None or not verified:
        messages.error(request, "No pending password reset request was found.")
        return redirect("hub:forgot_password")

    form_data = {}
    if request.method == "POST":
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")
        form_data = {"password": password, "confirm_password": confirm_password}

        if not password or not confirm_password:
            messages.error(request, "Please enter and confirm your new password.")
            return render(request, "reset_password.html", {"form_data": form_data, "email": user.email})

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "reset_password.html", {"form_data": form_data, "email": user.email})

        try:
            validate_password(password, user)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
            return render(request, "reset_password.html", {"form_data": form_data, "email": user.email})

        user.set_password(password)
        user.save()
        profile = get_object_or_404(UserProfile, user=user)
        profile.otp_code = ""
        profile.save(update_fields=["otp_code"])
        request.session.pop("pending_password_reset_user_id", None)
        request.session.pop("pending_password_reset_verified", None)
        request.session.pop("debug_otp_code", None)
        messages.success(request, "Your password has been reset. Please log in with your new password.")
        return redirect("hub:signin")

    return render(request, "reset_password.html", {"email": user.email, "form_data": form_data})


def signin(request):
    switching_account = request.GET.get("switch_account") == "1" or request.POST.get("switch_account") == "1"
    if request.user.is_authenticated and not switching_account:
        return redirect("hub:dashboard")

    next_url = _safe_next_url(request)

    if request.method == "POST":
        identifier = (
            request.POST.get("identifier")
            or request.POST.get("email")
            or request.POST.get("username")
            or ""
        ).strip()
        password = request.POST.get("password", "")
        form_data = {"email": identifier}

        if not identifier or not password:
            messages.error(request, "Please enter your email/username and password.")
            return render(request, "signin.html", {"form_data": form_data, "next": next_url, "switch_account": switching_account, "google_client_id": settings.GOOGLE_CLIENT_ID})

        User = get_user_model()
        user = None
        if "@" in identifier:
            try:
                user_obj = User.objects.get(email__iexact=identifier)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                user = authenticate(request, username=identifier, password=password)
        else:
            user = authenticate(request, username=identifier, password=password)
            if user is None:
                try:
                    user_obj = User.objects.get(username__iexact=identifier)
                    user = authenticate(request, username=user_obj.username, password=password)
                except User.DoesNotExist:
                    pass

        if user is None:
            messages.error(request, "Invalid email/username or password.")
            return render(request, "signin.html", {"form_data": form_data, "next": next_url, "switch_account": switching_account, "google_client_id": settings.GOOGLE_CLIENT_ID})

        if not user.is_active:
            messages.error(request, "This account is not active. Please verify your OTP before logging in.")
            return render(request, "signin.html", {"form_data": form_data, "next": next_url, "switch_account": switching_account, "google_client_id": settings.GOOGLE_CLIENT_ID})

        _start_authenticated_session(
            request,
            user,
            remember_me=_remember_me_enabled(request),
            entrypoint="signin",
        )
        if next_url:
            return redirect(next_url)
        return redirect("hub:dashboard")

    return render(request, "signin.html", {
        "next": next_url,
        "switch_account": switching_account,
        "form_data": {"email": request.GET.get("email", "")},
        "google_client_id": settings.GOOGLE_CLIENT_ID,
    })


@login_required
def account_continue(request):
    """Route the account modal's email to the existing-account or signup flow."""
    email = request.POST.get("email", request.GET.get("email", "")).strip().lower()
    next_url = _safe_next_url(request) or _dashboard_next_url()
    if not email:
        messages.error(request, "Enter an email address to continue.")
        return redirect(next_url)

    User = get_user_model()
    if User.objects.filter(email__iexact=email).exists():
        params = {"switch_account": "1", "email": email, "next": next_url}
        return redirect(f"{reverse('hub:signin')}?{urlencode(params)}")

    params = {"add_account": "1", "email": email, "next": next_url}
    return redirect(f"{reverse('hub:signup')}?{urlencode(params)}")


@login_required
@require_POST
def switch_account(request, user_id):
    raw_ids = request.session.get("account_switch_user_ids", [])
    try:
        account_ids = {int(value) for value in raw_ids}
    except (TypeError, ValueError):
        account_ids = set()

    if user_id not in account_ids:
        messages.error(request, "That account is not available in this session.")
        return redirect(_safe_next_url(request) or "hub:dashboard")

    target = get_object_or_404(get_user_model(), pk=user_id, is_active=True)
    _start_authenticated_session(
        request,
        target,
        remember_me=bool(request.session.get("remember_me", False)),
        entrypoint="account_switch",
    )
    return redirect(_safe_next_url(request) or "hub:dashboard")


def forgot_password(request):
    User = get_user_model()
    form_data = {}

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        form_data = {"email": email}

        if not email:
            messages.error(request, "Please enter your email address.")
            return render(request, "forgetpw.html", {"form_data": form_data})

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            messages.success(
                request,
                "If an account exists for that email, password reset instructions will be sent.",
            )
            return render(request, "forgetpw.html", {"form_data": form_data})

        email_sent, otp_code = _send_verification_otp(user, reset_flow=True)
        request.session["pending_password_reset_user_id"] = user.pk
        request.session["pending_password_reset_next"] = None
        _store_debug_otp(request, otp_code, email_sent=email_sent)
        _add_otp_delivery_message(request, email_sent=email_sent, reset_flow=True)
        return redirect("hub:verify_otp")

    return render(request, "forgetpw.html")


@login_required
def dashboard_search(request):
    q = request.GET.get("q", "").strip()
    posts = CommunityPost.objects.none()
    if q:
        posts = CommunityPost.objects.filter(body__icontains=q).select_related("author_user", "community").order_by("-created_at")[:20]
    user_communities = _community_cards(
        Community.objects.filter(memberships__user=request.user, is_archived=False).order_by("name")
    )
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    return render(request, "user-dashboard/sections/dashboard.html", {
        "display_name": _display_name(request.user),
        "active_page": "search",
        "section_template": "user-dashboard/sections/search_results.html",
        "profile": profile,
        "user": request.user,
        "today": timezone.localdate(),
        "notification_count": _unread_notification_count(request.user),
        "notifications": _notifications_for_user(request.user),
        "active_subscription": _active_subscription_for(request.user),
        "query": q,
        "search_posts": posts,
        "user_dashboard_sections": _user_dashboard_sections(),
        "user_communities": user_communities,
    })


@login_required
def dashboard(request, section="overview"):
    # The profile menu uses the dashboard section URL, while the profile page
    # itself needs the full profile context supplied by ``user_profile``.
    # Handle it before the generic section renderer tries to include the
    # standalone profile template.
    if section == "profile":
        return user_profile(request, username=request.user.username)
    if _is_platform_admin(request.user):
        return super_admin_dashboard(request, section=section)
    if section not in USER_DASHBOARD_SECTIONS:
        return redirect("hub:dashboard")
    if section == "overview" and request.path.rstrip("/").endswith("dashboard"):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        default_section = profile.personalization.get("default_dashboard_section", "overview")
        if default_section != "overview":
            return redirect("hub:dashboard_section", section=default_section)
    return user_dashboard(request, section=section)


def _dashboard_stats():
    return [
        {
            "label": "Total Users",
            "value": f"{get_user_model().objects.count():,}",
            "helper": "registered members",
            "icon": "fa-users",
        },
        {
            "label": "Communities",
            "value": f"{Community.objects.count():,}",
            "helper": "active spaces",
            "icon": "fa-compass",
        },
        {
            "label": "Memberships",
            "value": f"{CommunityMembership.objects.count():,}",
            "helper": "community joins",
            "icon": "fa-user-group",
        },
        {
            "label": "Posts Today",
            "value": f"{CommunityPost.objects.filter(created_at__date=timezone.localdate()).count():,}",
            "helper": "new updates",
            "icon": "fa-clipboard-list",
        },
    ]


def _compact_number(value):
    value = value or 0
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


def _build_dm_previews(user):
    """Build a dict mapping user_id -> last message text for DM dropdown."""
    threads = DirectMessageThread.objects.filter(
        Q(participant1=user) | Q(participant2=user)
    )
    previews = {}
    for thread in threads:
        other = thread.participant2 if thread.participant1 == user else thread.participant1
        last_msg = DirectMessage.objects.filter(thread=thread).order_by("-created_at").first()
        if last_msg:
            text = last_msg.body
            if len(text) > 60:
                text = text[:57] + "..."
            previews[other.pk] = text
    return previews


def _user_dashboard_stats(user=None):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    monthly_revenue = (
        PaymentTransaction.objects.filter(
            status=PaymentTransaction.SUCCESS,
            created_at__date__gte=month_start,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    # Count unique users in communities owned by the current user
    if user:
        total_users = get_user_model().objects.filter(
            community_memberships__community__owner=user
        ).distinct().count()
    else:
        total_users = get_user_model().objects.count()

    return [
        {
            "label": "Total Users",
            "value": _compact_number(total_users),
            "trend": "12.5",
            "direction": "up",
            "helper": "vs last month",
            "icon": "fa-users",
        },
        {
            "label": "Active Communities",
            "value": _compact_number(Community.objects.filter(is_archived=False).count()),
            "trend": "8.2",
            "direction": "up",
            "helper": "vs last month",
            "icon": "fa-compass",
        },
        {
            "label": "Posts Today",
            "value": _compact_number(CommunityPost.objects.filter(created_at__date=today).count()),
            "trend": "2.4",
            "direction": "down",
            "helper": "vs yesterday",
            "icon": "fa-clipboard-list",
        },
        {
            "label": "Monthly Revenue",
            "value": f"Rs. {_compact_number(float(monthly_revenue))}",
            "trend": "15.3",
            "direction": "up",
            "helper": "vs last month",
            "icon": "fa-credit-card",
        },
    ]


def _community_insights():
    recent_posts = CommunityPost.objects.select_related("community", "author_user")[:8]
    latest_post = recent_posts[0] if recent_posts else None
    top_community = (
        Community.objects.annotate(
            post_count=Count("posts", distinct=True),
            member_count=Count("memberships", distinct=True),
        )
        .filter(post_count__gt=0)
        .order_by("-post_count", "name")
        .first()
    )

    insights = []
    if latest_post:
        insights.append(
            {
                "title": "Latest Post",
                "body": f"{latest_post.title} in {latest_post.community or 'General'} by {latest_post.display_author}",
                "icon": "fa-bullhorn",
                "tone": "purple",
            }
        )
    else:
        insights.append(
            {
                "title": "First Post",
                "body": "Publish an update to start building community activity.",
                "icon": "fa-pen-to-square",
                "tone": "purple",
            }
        )

    if top_community:
        insights.append(
            {
                "title": "Most Active Community",
                "body": f"{top_community.name} has {top_community.post_count} post(s).",
                "icon": "fa-arrow-trend-up",
                "tone": "green",
            }
        )
    else:
        insights.append(
            {
                "title": "Communities",
                "body": "Create or join a community to start building activity.",
                "icon": "fa-layer-group",
                "tone": "green",
            }
        )

    insights.append(
        {
            "title": "Moderation",
            "body": "No moderation alerts are active right now.",
            "icon": "fa-shield-halved",
            "tone": "orange",
        }
    )
    return insights


def _creator_studio_context(user, selected_community_slug=""):
    """Return creator-only metrics and next steps for the Creator Studio."""
    now = timezone.now()
    today = timezone.localdate()
    activity_start = now - timedelta(days=30)
    previous_activity_start = now - timedelta(days=60)
    owned_communities = list(
        Community.objects.filter(owner=user, is_archived=False)
        .annotate(
            member_count=Count(
                "memberships",
                filter=~Q(memberships__user=user),
                distinct=True,
            ),
            post_count=Count("posts", distinct=True),
            event_count=Count("events", distinct=True),
        )
        .order_by("name")
    )
    owned_ids = [community.pk for community in owned_communities]
    creator_payout_summary = _creator_payout_summary(user, owned_ids)
    creator_payout_requests = PayoutRequest.objects.filter(
        requested_by=user,
    ).filter(
        Q(community_id__in=owned_ids) | Q(community__isnull=True)
    ).select_related("community", "processed_by")[:25]

    community_transaction_totals = {
        row["community_id"]: row
        for row in PaymentTransaction.objects.filter(
            community_id__in=owned_ids,
            status=PaymentTransaction.SUCCESS,
        )
        .values("community_id")
        .annotate(
            total=Sum("amount"),
            count=Count("id"),
        )
    }
    community_donation_totals = {
        row["post__community_id"]: row["total"]
        for row in PostDonation.objects.filter(
            post__community_id__in=owned_ids,
            post__author_user=user,
            status=PostDonation.SUCCESS,
        )
        .values("post__community_id")
        .annotate(total=Sum("amount"))
    }
    for community in owned_communities:
        transaction_summary = community_transaction_totals.get(community.pk, {})
        community.membership_revenue = transaction_summary.get("total") or Decimal("0.00")
        community.donation_revenue = community_donation_totals.get(community.pk) or Decimal("0.00")
        community.total_earnings = community.membership_revenue + community.donation_revenue
        community.payout_summary = _community_payout_summary(community)
        community.transaction_count = transaction_summary.get("count") or 0
        community.members_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=members"
        community.transactions_url = f"{reverse('hub:dashboard_section', kwargs={'section': 'payments'})}?community={quote(community.slug)}"
        community.payout_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=earnings#community-payout-request"
        community.settings_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=settings"
        community.insights_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=dashboard"
        community.posts_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts"
        community.compose_url = f"{community.posts_url}&compose=1"
        community.events_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=events"
        community.manage_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=manage"
        community.courses_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=courses"
        community.leaderboard_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=leaderboard"

    member_queryset = CommunityMembership.objects.filter(community_id__in=owned_ids).exclude(user=user)
    creator_member_count = (
        member_queryset
        .values("user")
        .distinct()
        .count()
    )
    creator_post_count = CommunityPost.objects.filter(community_id__in=owned_ids).count()
    creator_event_count = CommunityEvent.objects.filter(community_id__in=owned_ids).count()

    current_member_growth = (
        member_queryset.filter(joined_at__gte=activity_start).values("user").distinct().count()
    )
    previous_member_growth = (
        member_queryset.filter(
            joined_at__gte=previous_activity_start,
            joined_at__lt=activity_start,
        ).values("user").distinct().count()
    )
    if previous_member_growth:
        member_growth_percent = round(
            ((current_member_growth - previous_member_growth) / previous_member_growth) * 100
        )
        member_growth_label = f"{member_growth_percent:+d}% vs previous 30 days"
    elif current_member_growth:
        member_growth_percent = 100
        member_growth_label = f"+{current_member_growth} new in the last 30 days"
    else:
        member_growth_percent = 0
        member_growth_label = "No new members in the last 30 days"

    active_community_count = (
        Community.objects.filter(owner=user, is_archived=False, posts__created_at__gte=activity_start)
        .distinct()
        .count()
    )
    engagement_queryset = PostEngagement.objects.filter(post__community_id__in=owned_ids)
    comment_queryset = PostComment.objects.filter(post__community_id__in=owned_ids)
    creator_like_count = engagement_queryset.filter(engagement_type=PostEngagement.LIKE).count()
    creator_share_count = engagement_queryset.filter(engagement_type=PostEngagement.SHARE).count()
    creator_comment_count = comment_queryset.count()
    creator_engagement_count = creator_like_count + creator_share_count + creator_comment_count
    recent_post_count = CommunityPost.objects.filter(
        community_id__in=owned_ids,
        created_at__gte=activity_start,
    ).count()
    recent_engagement_count = (
        engagement_queryset.filter(created_at__gte=activity_start).count()
        + comment_queryset.filter(created_at__gte=activity_start).count()
    )

    membership_revenue = creator_payout_summary["membership_total"]
    donation_revenue = creator_payout_summary["donation_total"]
    creator_earnings = creator_payout_summary["lifetime_total"]
    selected_community = next(
        (community for community in owned_communities if community.slug == selected_community_slug),
        None,
    )
    creator_transactions = (
        PaymentTransaction.objects.select_related("community", "user")
        .filter(community_id__in=owned_ids)
        .order_by("-created_at")
    )
    if selected_community:
        creator_transactions = creator_transactions.filter(community=selected_community)
    creator_transaction_total = (
        creator_transactions.filter(status=PaymentTransaction.SUCCESS)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    creator_transactions = creator_transactions[:25]
    payout_account_connected = bool(
        getattr(user, "profile", None)
        and (
            user.profile.bank_account_number
            or user.profile.esewa_id
            or user.profile.khalti_id
        )
    )

    activity_trend = []
    for days_ago in range(6, -1, -1):
        day = today - timedelta(days=days_ago)
        posts = CommunityPost.objects.filter(community_id__in=owned_ids, created_at__date=day).count()
        interactions = (
            engagement_queryset.filter(created_at__date=day).count()
            + comment_queryset.filter(created_at__date=day).count()
        )
        activity_trend.append({
            "label": day.strftime("%a"),
            "posts": posts,
            "engagement": interactions,
            "total": posts + interactions,
        })
    activity_trend_max = max([item["total"] for item in activity_trend] or [1])
    for item in activity_trend:
        item["height"] = max(8, round((item["total"] / activity_trend_max) * 100)) if item["total"] else 8

    open_reports = ContentReport.objects.filter(community_id__in=owned_ids).exclude(
        status__in=[ContentReport.RESOLVED, ContentReport.DISMISSED]
    )
    studio_recent_reports = open_reports.select_related("community", "post").order_by("-ai_priority", "-created_at")[:4]
    primary_community = owned_communities[0] if owned_communities else None
    has_branding = bool(primary_community and (primary_community.cover_image or primary_community.tags))
    has_paid_community = any(community.requires_payment for community in owned_communities)

    studio_checklist = [
        {
            "title": "Create your first community",
            "description": "Give your audience a home base.",
            "complete": bool(owned_communities),
            "url": reverse("hub:create_community"),
            "icon": "fa-plus",
        },
        {
            "title": "Make your space recognisable",
            "description": "Add a cover image and topics.",
            "complete": has_branding,
            "url": f"{reverse('hub:community_detail', kwargs={'slug': primary_community.slug})}?tab=settings" if primary_community else reverse("hub:create_community"),
            "icon": "fa-wand-magic-sparkles",
        },
        {
            "title": "Invite your first member",
            "description": "Start with people who already trust you.",
            "complete": creator_member_count > 0,
            "url": f"{reverse('hub:community_detail', kwargs={'slug': primary_community.slug})}?tab=members" if primary_community else reverse("hub:create_community"),
            "icon": "fa-user-plus",
        },
        {
            "title": "Publish a welcome post",
            "description": "Set the tone for new members.",
            "complete": creator_post_count > 0,
            "url": f"{reverse('hub:community_detail', kwargs={'slug': primary_community.slug})}?tab=posts&compose=1" if primary_community else reverse("hub:create_community"),
            "icon": "fa-pen-nib",
        },
        {
            "title": "Schedule a live moment",
            "description": "Bring members together with an event.",
            "complete": creator_event_count > 0,
            "url": f"{reverse('hub:community_detail', kwargs={'slug': primary_community.slug})}?tab=events" if primary_community else reverse("hub:create_community"),
            "icon": "fa-calendar-days",
        },
        {
            "title": "Set up your membership offer",
            "description": "Choose a price and explain the value.",
            "complete": has_paid_community,
            "url": f"{reverse('hub:community_detail', kwargs={'slug': primary_community.slug})}?tab=settings" if primary_community else reverse("hub:create_community"),
            "icon": "fa-tags",
        },
    ]
    studio_checklist_completed = sum(item["complete"] for item in studio_checklist)

    upcoming_events = (
        CommunityEvent.objects.select_related("community", "organizer")
        .filter(community_id__in=owned_ids, starts_at__gte=timezone.now())
        .order_by("starts_at")[:6]
    )
    recent_members = (
        CommunityMembership.objects.select_related("community", "user", "user__profile")
        .filter(community_id__in=owned_ids)
        .exclude(user=user)
        .order_by("-joined_at")[:8]
    )
    creator_posts = (
        CommunityPost.objects.select_related("community")
        .filter(community_id__in=owned_ids)
        .annotate(
            like_count=Count("engagements", filter=Q(engagements__engagement_type=PostEngagement.LIKE), distinct=True),
            comment_count=Count("comments", distinct=True),
        )
        .order_by("-created_at")[:6]
    )
    studio_tiles = [
        {
            "label": "Total communities",
            "value": _compact_number(len(owned_communities)),
            "helper": "All communities you manage",
            "icon": "fa-layer-group",
            "tone": "indigo",
        },
        {
            "label": "Total members",
            "value": _compact_number(creator_member_count),
            "helper": member_growth_label,
            "icon": "fa-users",
            "tone": "emerald",
        },
        {
            "label": "Earnings & payouts",
            "value": f"Rs. {_compact_number(float(creator_earnings))}",
            "helper": "Across all communities",
            "icon": "fa-wallet",
            "tone": "amber",
        },
        {
            "label": "Posts & engagement",
            "value": _compact_number(creator_engagement_count),
            "helper": f"{_compact_number(creator_post_count)} posts / {_compact_number(recent_engagement_count)} interactions this month",
            "icon": "fa-chart-line",
            "tone": "rose",
        },
    ]
    studio_stack = [
        {"label": "Discussions", "status": "Live", "icon": "fa-comments"},
        {"label": "Courses / Library", "status": "Ready", "icon": "fa-graduation-cap"},
        {"label": "Events", "status": f"{creator_event_count} scheduled", "icon": "fa-calendar-days"},
        {"label": "Payments", "status": "Connected", "icon": "fa-credit-card"},
        {"label": "Member CRM", "status": "Profiles + DMs", "icon": "fa-address-book"},
        {"label": "Automation", "status": "Manual workflow", "icon": "fa-wand-magic-sparkles"},
    ]
    return {
        "owned_communities": owned_communities,
        "creator_member_count": creator_member_count,
        "creator_post_count": creator_post_count,
        "creator_event_count": creator_event_count,
        "creator_membership_revenue": membership_revenue,
        "creator_donation_revenue": donation_revenue,
        "creator_earnings": creator_earnings,
        "creator_payout_summary": creator_payout_summary,
        "creator_payout_requests": creator_payout_requests,
        "creator_payout_form": PayoutRequestForm(
            initial={"amount": creator_payout_summary["available_total"]}
            if creator_payout_summary["can_request"]
            else {}
        ),
        "creator_transaction_count": sum(community.transaction_count for community in owned_communities),
        "studio_community_transaction_totals": community_transaction_totals,
        "studio_payment_community": selected_community,
        "studio_creator_transactions": creator_transactions,
        "studio_creator_transaction_total": creator_transaction_total,
        "creator_like_count": creator_like_count,
        "creator_share_count": creator_share_count,
        "creator_comment_count": creator_comment_count,
        "creator_engagement_count": creator_engagement_count,
        "recent_post_count": recent_post_count,
        "recent_engagement_count": recent_engagement_count,
        "active_community_count": active_community_count,
        "member_growth_percent": member_growth_percent,
        "member_growth_label": member_growth_label,
        "payout_account_connected": payout_account_connected,
        "activity_trend": activity_trend,
        "studio_open_report_count": open_reports.count(),
        "studio_recent_reports": studio_recent_reports,
        "studio_checklist": studio_checklist,
        "studio_checklist_completed": studio_checklist_completed,
        "studio_checklist_total": len(studio_checklist),
        "studio_checklist_progress": round((studio_checklist_completed / len(studio_checklist)) * 100) if studio_checklist else 0,
        "studio_primary_community": primary_community,
        "studio_tiles": studio_tiles,
        "studio_stack": studio_stack,
        "studio_upcoming_events": upcoming_events,
        "studio_recent_members": recent_members,
        "studio_recent_posts": creator_posts,
    }


def _superadmin_insights():
    today = timezone.localdate()
    week_ago = today - timezone.timedelta(days=7)
    User = get_user_model()

    new_users_week = User.objects.filter(date_joined__date__gte=week_ago).count()
    total_users = User.objects.count()

    top_community = (
        Community.objects.annotate(
            member_count=Count("memberships", distinct=True),
            post_count=Count("posts", distinct=True),
        )
        .order_by("-member_count", "name")
        .first()
    )

    pending_reports = ContentReport.objects.filter(status=ContentReport.PENDING).count()
    posts_week = CommunityPost.objects.filter(created_at__date__gte=week_ago).count()

    insights = []

    growth_pct = round((new_users_week / max(total_users, 1)) * 100, 1)
    insights.append({
        "title": "User Growth",
        "body": f"{new_users_week} new users this week ({growth_pct}% of total)",
        "icon": "fa-user-plus",
        "tone": "violet",
        "value": new_users_week,
    })

    if top_community:
        insights.append({
            "title": "Top Community",
            "body": f"{top_community.name} — {top_community.member_count} members, {top_community.post_count} posts",
            "icon": "fa-trophy",
            "tone": "amber",
            "value": top_community.name,
        })
    else:
        insights.append({
            "title": "Communities",
            "body": "No communities yet. Encourage users to create spaces.",
            "icon": "fa-layer-group",
            "tone": "slate",
            "value": "0",
        })

    insights.append({
        "title": "Weekly Activity",
        "body": f"{posts_week} posts published in the last 7 days",
        "icon": "fa-chart-line",
        "tone": "emerald",
        "value": posts_week,
    })

    if pending_reports > 0:
        body = f"{pending_reports} pending report(s) need review"
    else:
        body = "All clear — no pending reports"
    insights.append({
        "title": "Moderation Queue",
        "body": body,
        "icon": "fa-shield-halved",
        "tone": "rose",
        "value": pending_reports,
    })

    return insights


def _superadmin_chart_data():
    today = timezone.localdate()
    days = []
    for i in range(6, -1, -1):
        days.append(today - timezone.timedelta(days=i))

    User = get_user_model()
    labels = [d.strftime("%a") for d in days]
    full_labels = [d.strftime("%b %d") for d in days]

    users_data = []
    communities_data = []
    posts_data = []

    for d in days:
        users_data.append(User.objects.filter(date_joined__date=d).count())
        communities_data.append(Community.objects.filter(created_at__date=d).count())
        posts_data.append(CommunityPost.objects.filter(created_at__date=d).count())

    return json.dumps({
        "labels": labels,
        "fullLabels": full_labels,
        "users": users_data,
        "communities": communities_data,
        "posts": posts_data,
    })


def _record_audit(actor, action, *, target="", details="", severity=AuditLog.INFO):
    AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target=target,
        details=details,
        severity=severity,
    )


def _notify(
    user,
    title,
    body,
    *,
    category=Notification.ACCOUNT,
    actor=None,
    notification_type=Notification.GENERAL,
):
    notification = Notification.objects.create(
        user=user,
        actor=actor,
        title=title,
        body=body,
        category=category,
        notification_type=notification_type,
    )
    profile = UserProfile.objects.filter(user=user).only(
        "email_verified",
        "personalization_preferences",
    ).first()
    preferences = profile.personalization if profile else {}
    if (
        not profile
        or not profile.email_verified
        or not user.email
        or not preferences.get("email_notifications", True)
        or preferences.get("notification_frequency", "immediate") == "off"
    ):
        return

    try:
        send_mail(
            f"Union notification: {notification.title}",
            f"{notification.body}\n\nManage email notifications in your Union account preferences.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException):
        logger.exception(
            "Failed to email notification %s to user %s",
            notification.pk,
            user.pk,
        )


def _process_mentions(text, actor, post=None, comment=None):
    import re
    User = get_user_model()
    mentions = re.findall(r'@(\w+)', text)
    for username in mentions:
        try:
            mentioned = User.objects.get(username__iexact=username)
            if mentioned == actor:
                continue
            _notify(
                mentioned,
                f"@{actor.username} mentioned you",
                f"You were mentioned in a post: {text[:80]}",
                category=Notification.COMMUNITY,
                actor=actor,
                notification_type=Notification.MENTION,
            )
        except User.DoesNotExist:
            pass


def _ensure_default_subscription_plans():
    defaults = [
        {
            "slug": SubscriptionPlan.BASIC,
            "name": "Basic",
            "price": "0.00",
            "community_limit": 1,
            "description": "See what Union can do",
            "feature_summary": "Create 1 community\nJoin public communities\nPost updates and comments\nBasic profile customization",
        },
        {
            "slug": SubscriptionPlan.STANDARD,
            "name": "Standard",
            "price": "499.00",
            "community_limit": 5,
            "description": "Grow your community presence",
            "feature_summary": "Create up to 5 communities\nPin and feature posts\nReceive payment notifications\nAdvanced profile & branding\nPriority support",
        },
        {
            "slug": SubscriptionPlan.PREMIUM,
            "name": "Premium",
            "price": "999.00",
            "community_limit": 20,
            "description": "Maximize your creator business",
            "feature_summary": "Create up to 20 communities\nAdvanced analytics dashboard\nCustom branding & themes\nPriority moderation review\nAPI access & integrations\nDedicated account support",
        },
    ]
    for plan in defaults:
        SubscriptionPlan.objects.get_or_create(slug=plan["slug"], defaults=plan)


def _active_subscription_for(user):
    return (
        UserSubscription.objects.select_related("plan")
        .filter(user=user, status=UserSubscription.ACTIVE)
        .order_by("-created_at")
        .first()
    )


def _community_limit_for(user):
    subscription = _active_subscription_for(user)
    if subscription:
        return subscription.community_limit_snapshot
    plan = SubscriptionPlan.objects.filter(slug=SubscriptionPlan.BASIC, is_active=True).first()
    return plan.community_limit if plan else 1


def _classify_report(reason, description):
    text = f"{reason} {description}".lower()
    if any(keyword in text for keyword in ["hate", "threat", "violence", "legal"]):
        return "Legal/compliance review", 5
    if any(keyword in text for keyword in ["harass", "abuse", "bully"]):
        return "Harassment", 4
    if any(keyword in text for keyword in ["spam", "scam", "fake", "fraud"]):
        return "Spam/fraud", 3
    if any(keyword in text for keyword in ["payment", "esewa", "khalti", "refund"]):
        return "Payment dispute", 3
    return "General moderation", 2


def _visible_posts_for(user):
    return (
        CommunityPost.objects.select_related("community", "author_user")
        .filter(
            Q(community__memberships__user=user)
            | Q(community__isnull=True)
            | Q(community__visibility=Community.PUBLIC)
        )
        .exclude(safety_status=CommunityPost.REMOVED)
        .distinct()
    )


def _record_community_join_payment(user, community):
    return PaymentTransaction.objects.create(
        user=user,
        community=community,
        gateway=PaymentTransaction.MANUAL,
        status=PaymentTransaction.SUCCESS,
        amount=community.membership_price,
        reference_code=f"UNION-COMMUNITY-{timezone.now().strftime('%Y%m%d%H%M%S')}-{user.pk}-{community.pk}",
        notes=f"Community join payment for {community.name}.",
    )


def _activate_subscription_after_payment(user, plan, payment, reference_code="", note=""):
    """Create the paid subscription only after the payment is verified."""
    if payment.status == PaymentTransaction.SUCCESS and payment.subscription_id:
        return payment.subscription

    UserSubscription.objects.filter(user=user, status=UserSubscription.ACTIVE).update(
        status=UserSubscription.CANCELLED
    )
    subscription = UserSubscription.objects.create(
        user=user,
        plan=plan,
        status=UserSubscription.ACTIVE,
        expires_at=timezone.now() + timedelta(days=30),
        community_limit_snapshot=plan.community_limit,
    )
    payment.subscription = subscription
    payment.status = PaymentTransaction.SUCCESS
    payment.reference_code = str(reference_code or payment.reference_code or "")
    if note:
        _append_payment_note(payment, note)
    update_fields = ["subscription", "status", "reference_code", "updated_at"]
    if note:
        update_fields.append("notes")
    payment.save(update_fields=update_fields)
    _notify(
        user,
        "Payment confirmed",
        f"Your {plan.name} subscription is active. Reference: {payment.reference_code}",
        category=Notification.PAYMENT,
    )
    _record_audit(user, "Purchased subscription", target=plan.name)
    return subscription


def _record_local_subscription_payment(user, plan):
    payment = PaymentTransaction.objects.create(
        user=user,
        plan=plan,
        gateway=PaymentTransaction.MANUAL,
        status=PaymentTransaction.SUCCESS,
        amount=plan.price,
        reference_code=f"UNION-{timezone.now().strftime('%Y%m%d%H%M%S')}-{user.pk}",
        notes="Local sandbox transaction record.",
    )
    _activate_subscription_after_payment(user, plan, payment)
    return payment


def _community_join_redirect(request, community):
    if community.requires_payment and esewa_is_configured():
        return redirect("hub:esewa_community_payment_start", community_id=community.pk)
    if community.requires_payment and not settings.DEBUG:
        messages.error(request, "Online payments are not configured yet. Please try again later.")
        return redirect("hub:dashboard_section", section="communities")
    return None


def _community_join_after_payment(user, community, payment):
    membership, created = CommunityMembership.objects.get_or_create(
        community=community,
        user=user,
        defaults={"role": CommunityMembership.MEMBER},
    )
    if created:
        _notify(
            user,
            "Payment confirmed",
            f"Your Rs. {payment.amount} payment for {community.name} was recorded.",
            category=Notification.PAYMENT,
        )
        _notify(user, "Community joined", f"You joined {community.name}.", category=Notification.COMMUNITY)
        _record_audit(user, "Joined community", target=community.name)
    return membership


@login_required
@require_POST
def esewa_community_payment_start(request, community_id):
    community = get_object_or_404(
        Community.objects.select_related("owner"),
        pk=community_id,
        visibility=Community.PUBLIC,
        is_archived=False,
    )
    user = request.user
    if community.owner_id == user.id or CommunityMembership.objects.filter(community=community, user=user).exists():
        return redirect("hub:community_detail", slug=community.slug)
    if not community.requires_payment:
        CommunityMembership.objects.create(community=community, user=user)
        return redirect("hub:community_detail", slug=community.slug)
    try:
        if not esewa_is_configured():
            raise EsewaConfigurationError("eSewa is not configured.")
        # eSewa requires a new transaction UUID for every payment attempt.
        # Reusing a pending local order after a refresh/retry makes eSewa
        # reject the request with "Duplicate transaction UUID".
        PaymentTransaction.objects.filter(
            user=user,
            community=community,
            gateway=PaymentTransaction.ESEWA,
            status=PaymentTransaction.PENDING,
        ).update(
            status=PaymentTransaction.FAILED,
            notes=f"Superseded by a new eSewa payment attempt for {community.name}.",
            updated_at=timezone.now(),
        )
        payment = PaymentTransaction.objects.create(
            user=user,
            community=community,
            gateway=PaymentTransaction.ESEWA,
            status=PaymentTransaction.PENDING,
            amount=community.membership_price,
            transaction_uuid=f"UNION-{uuid4().hex}",
            notes=f"eSewa community join payment for {community.name}.",
        )
        form_data = build_payment_data(
            payment.amount,
            payment.transaction_uuid,
            request.build_absolute_uri(reverse("hub:esewa_payment_success")),
            request.build_absolute_uri(reverse("hub:esewa_payment_failure")),
        )
    except EsewaConfigurationError:
        if settings.DEBUG:
            payment = _record_community_join_payment(user, community)
            _community_join_after_payment(user, community, payment)
            messages.warning(request, "eSewa is not configured; local development payment mode was used.")
            return redirect("hub:community_detail", slug=community.slug)
        messages.error(request, "Online payments are not configured yet. Please try again later.")
        return redirect("hub:dashboard_section", section="communities")
    return render(
        request,
        "payments/esewa_redirect.html",
        {
            "esewa_form_url": settings.ESEWA_FORM_URL,
            "form_data": form_data,
            "payment_label": f"joining {community.name}",
            "community": community,
        },
    )


@login_required
@require_POST
def esewa_subscription_payment_start(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id, is_active=True)
    user = request.user
    active_subscription = _active_subscription_for(user)

    if active_subscription and active_subscription.plan_id == plan.pk:
        messages.success(request, f"Your {plan.name} subscription is already active.")
        return redirect("hub:dashboard_section", section="subscriptions")

    if plan.price <= 0:
        _record_local_subscription_payment(user, plan)
        messages.success(request, f"Your {plan.name} plan is active.")
        return redirect("hub:dashboard_section", section="subscriptions")

    try:
        if not esewa_is_configured():
            raise EsewaConfigurationError("eSewa is not configured.")

        PaymentTransaction.objects.filter(
            user=user,
            plan=plan,
            gateway=PaymentTransaction.ESEWA,
            status=PaymentTransaction.PENDING,
        ).update(
            status=PaymentTransaction.FAILED,
            notes=f"Superseded by a new eSewa subscription payment attempt for {plan.name}.",
            updated_at=timezone.now(),
        )
        payment = PaymentTransaction.objects.create(
            user=user,
            plan=plan,
            gateway=PaymentTransaction.ESEWA,
            status=PaymentTransaction.PENDING,
            amount=plan.price,
            transaction_uuid=f"UNION-{uuid4().hex}",
            notes=f"eSewa subscription payment for {plan.name}.",
        )
        form_data = build_payment_data(
            payment.amount,
            payment.transaction_uuid,
            request.build_absolute_uri(reverse("hub:esewa_payment_success")),
            request.build_absolute_uri(reverse("hub:esewa_payment_failure")),
        )
    except EsewaConfigurationError:
        if settings.DEBUG:
            payment = _record_local_subscription_payment(user, plan)
            messages.warning(request, "eSewa is not configured; local development payment mode was used.")
            return redirect("hub:dashboard_section", section="subscriptions")
        messages.error(request, "Online payments are not configured yet. Please try again later.")
        return redirect("hub:dashboard_section", section="subscriptions")

    return render(
        request,
        "payments/esewa_redirect.html",
        {
            "esewa_form_url": settings.ESEWA_FORM_URL,
            "form_data": form_data,
            "payment_label": f"the {plan.name} plan",
            "plan": plan,
        },
    )


def _esewa_response_payload(request):
    encoded_data = request.GET.get("data") or request.POST.get("data")
    return decode_response(encoded_data)


def _esewa_payload_text(payload, *keys):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _append_payment_note(payment, note):
    payment.notes = "\n".join(part for part in [payment.notes.strip(), note] if part)


def _complete_esewa_community_payment(payment, reference_code="", note=""):
    payment.status = PaymentTransaction.SUCCESS
    payment.reference_code = str(reference_code or "")
    if note:
        _append_payment_note(payment, note)
    update_fields = ["status", "reference_code", "updated_at"]
    if note:
        update_fields.append("notes")
    payment.save(update_fields=update_fields)
    _community_join_after_payment(payment.user, payment.community, payment)


def _complete_esewa_subscription_payment(payment, reference_code="", note=""):
    _activate_subscription_after_payment(
        payment.user,
        payment.plan,
        payment,
        reference_code=reference_code,
        note=note,
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def esewa_payment_success(request):
    try:
        payload = _esewa_response_payload(request)
        verify_response_signature(payload)
        payment = PaymentTransaction.objects.select_related("community", "plan", "subscription").get(
            transaction_uuid=payload.get("transaction_uuid"),
            gateway=PaymentTransaction.ESEWA,
        )
        if payload.get("product_code") != settings.ESEWA_PRODUCT_CODE:
            raise EsewaVerificationError("eSewa product code does not match.")
        if Decimal(str(payload.get("total_amount"))) != payment.amount:
            raise EsewaVerificationError("eSewa amount does not match the order.")
        if payment.community_id is None and payment.plan_id is None:
            raise EsewaVerificationError("This eSewa transaction is not linked to a community or subscription plan.")
        if payload.get("status") and payload.get("status") != "COMPLETE":
            raise EsewaVerificationError("eSewa callback is not complete.")
        try:
            status_result = esewa_status_check(payment.amount, payment.transaction_uuid)
        except EsewaStatusCheckError as exc:
            _append_payment_note(payment, f"eSewa status check unavailable after signed COMPLETE callback: {exc}")
            payment.save(update_fields=["notes", "updated_at"])
            if not settings.ESEWA_ALLOW_SIGNED_CALLBACK_FALLBACK:
                messages.error(
                    request,
                    (
                        "eSewa payment was signed, but the status check is unavailable. No subscription was activated yet."
                        if payment.plan_id and not payment.community_id
                        else "eSewa payment was signed, but the status check is unavailable. No membership was created yet."
                    ),
                )
                return redirect(
                    "hub:dashboard_section",
                    section="subscriptions" if payment.plan_id and not payment.community_id else "payments",
                )

            reference_code = payload.get("transaction_code")
            note = "Accepted signed eSewa COMPLETE callback because status check is unavailable in DEBUG mode."
            if payment.community_id:
                _complete_esewa_community_payment(payment, reference_code, note)
                success_message = f"Payment accepted in local test mode. You joined {payment.community.name}."
                success_redirect = "hub:community_detail"
            else:
                _complete_esewa_subscription_payment(payment, reference_code, note)
                success_message = f"Payment accepted in local test mode. Your {payment.plan.name} subscription is active."
                success_redirect = "hub:dashboard_section"
            messages.success(request, success_message)
            if request.user.is_authenticated and request.user.id == payment.user_id:
                if success_redirect == "hub:community_detail":
                    return redirect(success_redirect, slug=payment.community.slug)
                return redirect(success_redirect, section="subscriptions")
            return redirect("hub:signin")

        if status_result.get("status") != "COMPLETE":
            messages.warning(request, "eSewa has not marked this payment complete yet. Please try again shortly.")
            return redirect(
                "hub:dashboard_section",
                section="subscriptions" if payment.plan_id and not payment.community_id else "payments",
            )
        status_product_code = status_result.get("scd") or status_result.get("product_code")
        if status_product_code and status_product_code != settings.ESEWA_PRODUCT_CODE:
            raise EsewaVerificationError("eSewa status product code does not match the order.")
        status_amount = status_result.get("totalAmount")
        if status_amount is None:
            status_amount = status_result.get("total_amount")
        if status_amount is not None and Decimal(str(status_amount)) != payment.amount:
            raise EsewaVerificationError("eSewa status amount does not match the order.")
        reference_code = (
            status_result.get("refId")
            or status_result.get("ref_id")
            or status_result.get("pid")
            or payload.get("transaction_code")
        )
        if payment.community_id:
            _complete_esewa_community_payment(payment, reference_code)
            success_message = f"Payment confirmed. You joined {payment.community.name}."
            success_redirect = "hub:community_detail"
        else:
            _complete_esewa_subscription_payment(payment, reference_code)
            success_message = f"Payment confirmed. Your {payment.plan.name} subscription is active."
            success_redirect = "hub:dashboard_section"
        messages.success(request, success_message)
        if request.user.is_authenticated and request.user.id == payment.user_id:
            if success_redirect == "hub:community_detail":
                return redirect(success_redirect, slug=payment.community.slug)
            return redirect(success_redirect, section="subscriptions")
        return redirect("hub:signin")
    except (PaymentTransaction.DoesNotExist, EsewaVerificationError, ValueError, TypeError) as exc:
        logger.warning("Rejected eSewa success callback: %s", exc)
        messages.error(request, "We could not verify this eSewa payment. No subscription or membership was activated.")
        return redirect("hub:dashboard_section", section="payments")
    except Exception:
        logger.exception("Unexpected eSewa success callback failure.")
        messages.error(request, "Payment verification is temporarily unavailable. Please try again.")
        return redirect("hub:dashboard_section", section="payments")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def esewa_payment_failure(request):
    try:
        payload = _esewa_response_payload(request)
        if payload.get("signature") and payload.get("signed_field_names"):
            verify_response_signature(payload)

        payment = PaymentTransaction.objects.filter(
            transaction_uuid=payload.get("transaction_uuid"),
            gateway=PaymentTransaction.ESEWA,
        ).first()
        is_subscription_payment = bool(payment and payment.plan_id and not payment.community_id)
        retry_section = "subscriptions" if is_subscription_payment else "communities"
        callback_status = _esewa_payload_text(payload, "status", "transaction_status").upper()
        reason = _esewa_payload_text(payload, "error_message", "message", "response_message", "detail")
        note_parts = ["eSewa failure callback"]
        if callback_status:
            note_parts.append(f"status={callback_status}")
        if reason:
            note_parts.append(f"reason={reason}")
        note = " ".join(note_parts) + "."

        if payment and callback_status in {"PENDING", "AMBIGIOUS", "AMBIGUOUS"}:
            _append_payment_note(payment, note)
            payment.save(update_fields=["notes", "updated_at"])
            messages.warning(
                request,
                (
                    "eSewa returned this payment as pending. No subscription was activated yet; please retry after eSewa completes the transaction."
                    if is_subscription_payment
                    else "eSewa returned this payment as pending. No membership was created yet; please retry after eSewa completes the transaction."
                ),
            )
            return redirect("hub:dashboard_section", section=retry_section)

        if payment and payment.status == PaymentTransaction.PENDING:
            payment.status = PaymentTransaction.FAILED
            _append_payment_note(payment, note)
            payment.save(update_fields=["status", "notes", "updated_at"])

        detail = " - ".join(part for part in [callback_status, reason] if part)
        if detail:
            messages.error(
                request,
                f"eSewa did not complete this payment ({detail}). "
                + ("No subscription was activated." if is_subscription_payment else "No membership was created."),
            )
        else:
            messages.error(
                request,
                "eSewa did not complete this payment. "
                + ("No subscription was activated." if is_subscription_payment else "No membership was created."),
            )
        return redirect("hub:dashboard_section", section=retry_section)
    except EsewaVerificationError as exc:
        logger.warning("Rejected eSewa failure callback: %s", exc)
        messages.error(
            request,
            "We could not verify the eSewa payment response. "
            + ("No subscription was activated." if 'is_subscription_payment' in locals() and is_subscription_payment else "No membership was created."),
        )
        return redirect(
            "hub:dashboard_section",
            section="subscriptions" if 'is_subscription_payment' in locals() and is_subscription_payment else "payments",
        )
    except Exception:
        logger.info("eSewa failure callback did not include a usable transaction.", exc_info=True)
        messages.error(
            request,
            "eSewa did not complete this payment. "
            + ("No subscription was activated." if 'is_subscription_payment' in locals() and is_subscription_payment else "No membership was created."),
        )
        return redirect(
            "hub:dashboard_section",
            section="subscriptions" if 'is_subscription_payment' in locals() and is_subscription_payment else "communities",
        )


@login_required
def create_community(request):
    if _is_platform_admin(request.user):
        return redirect("hub:dashboard")

    user = request.user
    display_name = _display_name(user)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    _ensure_default_subscription_plans()
    community_limit = _community_limit_for(user)
    owned_count = Community.objects.filter(owner=user, is_archived=False).count()

    if owned_count >= community_limit:
        messages.error(request, "Your current subscription limit has been reached.")
        return redirect("hub:dashboard_section", section="subscriptions")

    if request.method == "POST":
        community_form = CommunityCreateForm(request.POST)
        if community_form.is_valid():
            community = community_form.save(commit=False)
            community.owner = user
            community.save()
            CommunityMembership.objects.get_or_create(
                community=community,
                user=user,
                defaults={"role": CommunityMembership.OWNER},
            )
            _record_audit(user, "Created community", target=community.name)
            messages.success(request, f"{community.name} has been created.")
            return redirect("hub:dashboard_section", section="communities")
        messages.error(request, "Please correct the community form and try again.")
    else:
        community_form = CommunityCreateForm()

    user_communities = _community_cards(
        Community.objects.filter(memberships__user=user, is_archived=False).exclude(slug="").order_by("name")
    )

    return render(
        request,
        "user-dashboard/sections/dashboard.html",
        {
            "display_name": display_name,
            "active_page": "communities",
            "section_template": "user-dashboard/sections/community_create.html",
            "profile": profile,
            "user": user,
            "today": timezone.localdate(),
            "notification_count": _unread_notification_count(user),
            "notifications": _notifications_for_user(user),
            "active_subscription": _active_subscription_for(user),
            "community_form": community_form,
            "community_limit": community_limit,
            "owned_community_count": owned_count,
            "session_info": _session_context(request),
            "user_dashboard_sections": _user_dashboard_sections(),
            "user_communities": user_communities,
        },
    )


@login_required
def community_detail(request, slug):
    if _is_platform_admin(request.user):
        return redirect("hub:dashboard")

    user = request.user
    display_name = _display_name(user)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    community = get_object_or_404(_visible_communities_for(user), slug=slug)
    membership = CommunityMembership.objects.filter(community=community, user=user).first()
    is_owner = community.owner_id == user.id
    is_member = bool(membership) or is_owner
    role = CommunityMembership.OWNER if is_owner else (membership.role if membership else None)
    is_manager = role in {CommunityMembership.OWNER, CommunityMembership.MODERATOR}
    can_edit_community = bool(role and _community_has_permission(user, community, "manage_settings"))
    can_manage_members = bool(is_owner)
    can_manage_roles = bool(is_owner)
    can_view_payments = bool(role and _community_has_permission(user, community, "view_payments"))
    can_request_payout = bool(role and _community_has_permission(user, community, "request_payout"))
    community_form = CommunityForm(instance=community)
    community_tabs = {"posts", "dashboard", "getting-started", "courses", "events", "members", "leaderboard", "about"}
    if can_edit_community:
        community_tabs.add("settings")
    if can_view_payments:
        community_tabs.add("payments")
        community_tabs.add("earnings")
    if _community_has_permission(user, community, "moderate_content"):
        community_tabs.add("moderation")
    if can_manage_roles:
        community_tabs.add("manage")
    active_community_tab = request.GET.get("tab", "posts")
    if active_community_tab not in community_tabs:
        active_community_tab = "posts"
    community_url = reverse("hub:community_detail", kwargs={"slug": community.slug})
    posts_url = f"{community_url}?tab=posts"
    settings_url = f"{community_url}?tab=settings"
    editing_post = None
    compose_mode = request.GET.get("compose") == "1"
    payout_form = PayoutRequestForm()
    if request.method == "GET":
        edit_post_id = request.GET.get("edit")
        if edit_post_id:
            try:
                candidate = CommunityPost.objects.get(pk=edit_post_id, community=community)
                if candidate.author_user_id == user.id or is_manager:
                    editing_post = candidate
            except CommunityPost.DoesNotExist:
                editing_post = None

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "delete_community":
            if not _community_has_permission(user, community, "delete_community"):
                messages.error(request, "Only the community owner can delete this community.")
            else:
                community_name = community.name
                community.delete()
                _record_audit(user, "Deleted community", target=community_name, severity=AuditLog.WARNING)
                messages.success(request, f"{community_name} has been deleted.")
                return HttpResponseRedirect(_dashboard_section_url("communities"))

        elif action == "update_community":
            if not can_edit_community:
                messages.error(request, "Only the community owner can update community settings.")
            else:
                community_form = CommunityForm(request.POST, request.FILES, instance=community)
                if community_form.is_valid():
                    updated_community = community_form.save(commit=False)
                    if not is_owner:
                        # Admins can manage the community experience, but
                        # payment pricing and plan copy belong to the owner.
                        for field_name in (
                            "membership_plan_name",
                            "membership_plan_description",
                            "membership_price",
                        ):
                            setattr(updated_community, field_name, getattr(community, field_name))
                    updated_community.save()
                    _record_audit(user, "Updated community", target=community.name)
                    messages.success(request, "Community settings updated.")
                    return HttpResponseRedirect(settings_url)
                messages.error(request, "Please correct the community settings and try again.")

        elif action == "request_payout":
            if not can_request_payout:
                messages.error(request, "Only the community owner can request a community payout.")
            else:
                payout_form = PayoutRequestForm(request.POST)
                if payout_form.is_valid():
                    payout_summary = _community_payout_summary(community)
                    payout_amount = payout_form.cleaned_data["amount"]
                    payout_method = _payout_method_for_profile(profile)
                    if not profile.kyc_verified:
                        payout_form.add_error(None, "Complete identity verification before requesting a payout.")
                    elif not payout_method:
                        payout_form.add_error(None, "Add a bank account, eSewa ID, or Khalti ID before requesting a payout.")
                    elif payout_amount > payout_summary["available_total"]:
                        payout_form.add_error(
                            "amount",
                            f"You can request up to Rs. {payout_summary['available_total']:,.2f} from {community.name}.",
                        )
                    else:
                        PayoutRequest.objects.create(
                            community=community,
                            requested_by=user,
                            amount=payout_amount,
                            payout_method=payout_method,
                            creator_note=payout_form.cleaned_data.get("note", "").strip(),
                        )
                        _record_audit(user, "Requested community payout", target=community.name)
                        messages.success(request, f"Your {community.name} payout request has been submitted for review.")
                        return HttpResponseRedirect(f"{community_url}?tab=earnings#community-payout-request")
                messages.error(request, "Please review the community payout request details.")

        elif action == "create_post":
            if not is_member:
                response = _ajax_error_response(request, "Join this community before posting.")
                if response:
                    return response
                return HttpResponseRedirect(posts_url)
            elif not community.allow_member_posts and not is_manager:
                response = _ajax_error_response(request, "Only community managers can post here right now.")
                if response:
                    return response
                return HttpResponseRedirect(posts_url)
            else:
                # create_post handling

                title = request.POST.get("title", "").strip()
                body = request.POST.get("body", "").strip()
                attachment_file = request.FILES.get("attachment_file")
                attachment_url = request.POST.get("attachment_url", "").strip()
                if not body and not attachment_file and not attachment_url:
                    response = _ajax_error_response(request, "Share text, an image, or a video before posting.")
                    if response:
                        return response
                    return HttpResponseRedirect(posts_url)
                else:
                    attachment_error = _post_attachment_error(attachment_file)
                    if attachment_error:
                        response = _ajax_error_response(request, attachment_error)
                        if response:
                            return response
                        return HttpResponseRedirect(posts_url)
                    if not title:
                        title = body[:80] if body else "Post attachment"
                    accept_donations = request.POST.get("accept_donations") == "on"
                    # A member whose posting privileges require review can still
                    # submit their post, but it remains out of the public feed
                    # until a moderator confirms it.
                    needs_review = bool(
                        membership
                        and not is_manager
                        and not membership.can_post_without_approval
                    )
                    requested_content_type = request.POST.get("content_type", CommunityPost.UPDATE)
                    if requested_content_type not in dict(CommunityPost.CONTENT_TYPE_CHOICES):
                        requested_content_type = CommunityPost.UPDATE
                    if attachment_file and getattr(attachment_file, "content_type", "").startswith("video/"):
                        requested_content_type = CommunityPost.VIDEO
                    try:
                        post = CommunityPost.objects.create(
                            community=community,
                            author_user=user,
                            author=display_name,
                            title=title,
                            category=request.POST.get("category", "").strip() or "Community",
                            content_type=requested_content_type,
                            body=body,
                            attachment_file=attachment_file,
                            attachment_url=attachment_url,
                            accept_donations=accept_donations,
                            approval_status=(
                                CommunityPost.APPROVAL_PENDING
                                if needs_review
                                else CommunityPost.APPROVAL_APPROVED
                            ),
                        )
                    except OSError:
                        logger.exception("Unable to store community post attachment.")
                        response = _ajax_error_response(request, "Unable to store the attachment. Please try again.", status=500)
                        if response:
                            return response
                        return HttpResponseRedirect(posts_url)
                    try:
                        if not post.ai_summary:
                            post.ai_summary = _generate_ai_summary(post.body)
                            post.save(update_fields=["ai_summary"])
                    except Exception:
                        logger.exception("Failed to set ai_summary.")
                    try:
                        _process_mentions(body, user, post=post)
                    except Exception:
                        logger.exception("Failed to process mentions.")
                    try:
                        rep, _ = UserReputation.objects.get_or_create(user=user)
                        rep.add_xp(10)
                    except Exception:
                        logger.exception("Failed to add XP.")
                    try:
                        check_and_award(user)
                    except Exception:
                        logger.exception("Failed to check and award badges.")
                    try:
                        _record_audit(user, "Created community post", target=community.name)
                    except Exception:
                        logger.exception("Failed to record audit.")
                    messages.success(request, "Post submitted for moderator review." if needs_review else "Post published.")
                    if _is_ajax_request(request):
                        return HttpResponse("Post published.", status=200)
                    return HttpResponseRedirect(posts_url)

        elif action == "edit_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            if post.author_user_id != user.id and not is_manager:
                messages.error(request, "You cannot edit this post.")
            else:
                title = request.POST.get("title", "").strip()
                body = request.POST.get("body", "").strip()
                attachment_file = request.FILES.get("attachment_file")
                attachment_url = request.POST.get("attachment_url", "").strip()
                if not body and not attachment_file and not attachment_url:
                    messages.error(request, "Update text, an image, or a video before saving.")
                else:
                    attachment_error = _post_attachment_error(attachment_file)
                    if attachment_error:
                        messages.error(request, attachment_error)
                        editing_post = post
                    else:
                        if not title:
                            title = body[:80] if body else "Post update"
                        post.title = title
                        post.body = body
                        if attachment_file:
                            post.attachment_file = attachment_file
                        if attachment_url:
                            post.attachment_url = attachment_url
                        post.save()
                        _record_audit(user, "Edited community post", target=community.name)
                        messages.success(request, "Post updated.")
                        return HttpResponseRedirect(posts_url)
                editing_post = post

        elif action == "delete_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            scope = request.POST.get("scope", "everyone").strip().lower()
            if scope == "me":
                # hide for current user only
                try:
                    HiddenPost.objects.get_or_create(post=post, user=user)
                    messages.success(request, "Post removed for you.")
                except Exception:
                    messages.error(request, "Could not remove post for you.")
                return HttpResponseRedirect(posts_url)
            else:
                # delete for everyone (existing behavior) - require ownership or manager
                if post.author_user_id != user.id and not is_manager:
                    messages.error(request, "You cannot delete this post.")
                else:
                    post.delete()
                    _record_audit(user, "Deleted community post", target=community.name)
                    messages.success(request, "Post deleted for everyone.")
                    return HttpResponseRedirect(posts_url)

        elif action == "report_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            reason = request.POST.get("reason", "inappropriate").strip() or "inappropriate"
            description = request.POST.get("description", "").strip() or "Reported from community post."
            category, priority = _classify_report(reason, description)
            report = ContentReport.objects.create(
                reporter=user,
                post=post,
                community=community,
                reported_user=post.author_user,
                reason=reason,
                description=description,
                ai_category=category,
                ai_priority=priority,
            )
            messages.success(request, "Post reported to moderators.")
            return HttpResponseRedirect(posts_url)

        elif action == "join_community":
            if community.visibility != Community.PUBLIC:
                messages.error(request, "This community is private.")
            elif CommunityMembership.objects.filter(community=community, user=user).exists():
                messages.success(request, f"You are already a member of {community.name}.")
            else:
                payment_redirect = _community_join_redirect(request, community)
                if payment_redirect:
                    return payment_redirect
                if community.requires_payment:
                    transaction = _record_community_join_payment(user, community)
                    _notify(
                        user,
                        "Payment confirmed",
                        f"Your Rs. {transaction.amount} payment for {community.name} was recorded.",
                        category=Notification.PAYMENT,
                    )
                CommunityMembership.objects.create(community=community, user=user)
                _notify(user, "Community joined", f"You joined {community.name}.", category=Notification.COMMUNITY)
                _record_audit(user, "Joined community", target=community.name)
                messages.success(request, f"You joined {community.name}.")
                return HttpResponseRedirect(posts_url)

        elif action == "leave_community":
            if is_owner:
                messages.error(request, "Owners cannot leave their own community. Delete it from the communities dashboard.")
            else:
                CommunityMembership.objects.filter(community=community, user=user).delete()
                _record_audit(user, "Left community", target=community.name)
                messages.success(request, f"You left {community.name}.")
                return redirect("hub:dashboard_section", section="communities")

        elif action == "create_announcement":
            if not _community_has_permission(user, community, "moderate_content"):
                messages.error(request, "Only community managers can create announcements.")
            else:
                title = request.POST.get("title", "").strip()
                body = request.POST.get("body", "").strip()
                is_pinned = request.POST.get("is_pinned") == "on"
                if not title or not body:
                    messages.error(request, "Add both an announcement title and body.")
                else:
                    announcement = Announcement.objects.create(
                        community=community,
                        title=title,
                        body=body,
                        created_by=user,
                        is_pinned=is_pinned,
                        is_active=True,
                    )
                    _record_audit(user, "Created community announcement", target=community.name)
                    messages.success(request, "Announcement published.")
                    return HttpResponseRedirect(f"{community_url}?room={request.POST.get('room_id', request.GET.get('room', ''))}")

        elif action == "create_event":
            if not _community_has_permission(user, community, "manage_events"):
                messages.error(request, "Only community managers can create events.")
            else:
                title = request.POST.get("title", "").strip()
                description = request.POST.get("description", "").strip()
                starts_at = parse_datetime(request.POST.get("starts_at", "").strip())
                ends_at = parse_datetime(request.POST.get("ends_at", "").strip())
                link_url = request.POST.get("link_url", "").strip()

                if starts_at and timezone.is_naive(starts_at):
                    starts_at = timezone.make_aware(starts_at, timezone.get_current_timezone())
                if ends_at and timezone.is_naive(ends_at):
                    ends_at = timezone.make_aware(ends_at, timezone.get_current_timezone())

                if not title or not description or starts_at is None:
                    messages.error(request, "Add an event title, description, and start time.")
                elif ends_at and ends_at <= starts_at:
                    messages.error(request, "Event end time must be after the start time.")
                else:
                    CommunityEvent.objects.create(
                        community=community,
                        organizer=user,
                        title=title,
                        description=description,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        link_url=link_url,
                    )
                    _record_audit(user, "Created community event", target=community.name)
                    messages.success(request, "Event added to the community calendar.")
                    return HttpResponseRedirect(f"{community_url}?tab=events")

        elif action == "set_member_role":
            membership_id = request.POST.get("membership_id")
            target_membership = CommunityMembership.objects.filter(
                community=community,
                pk=membership_id,
            ).select_related("user").first()
            new_role = request.POST.get("role", "").strip().lower()
            if not can_manage_roles:
                messages.error(request, "Only the community owner can manage moderator access.")
            elif not target_membership or target_membership.role == CommunityMembership.OWNER:
                messages.error(request, "The community owner role cannot be changed here.")
            elif new_role not in {CommunityMembership.MODERATOR, CommunityMembership.MEMBER}:
                messages.error(request, "Choose a valid community role.")
            else:
                target_membership.role = new_role
                target_membership.save(update_fields=["role"])
                _record_audit(user, "Updated community member role", target=f"{target_membership.user.username} in {community.name}")
                messages.success(request, f"{target_membership.user.username} is now a {target_membership.get_role_display().lower()}.")
            destination = request.POST.get("next", "manage")
            if destination not in {"posts", "manage"}:
                destination = "manage"
            return HttpResponseRedirect(f"{community_url}?tab={destination}")

        elif action in {"approve_post", "reject_post"}:
            if not _community_has_permission(user, community, "moderate_content"):
                messages.error(request, "Only moderators can review member posts.")
            else:
                post = get_object_or_404(
                    CommunityPost.objects.filter(
                        community=community,
                        approval_status=CommunityPost.APPROVAL_PENDING,
                    ),
                    pk=request.POST.get("post_id"),
                )
                if action == "approve_post":
                    post.approval_status = CommunityPost.APPROVAL_APPROVED
                    post.approved_by = user
                    post.approved_at = timezone.now()
                    post.rejection_reason = ""
                    post.save(update_fields=["approval_status", "approved_by", "approved_at", "rejection_reason"])
                    _notify(post.author_user, "Post approved", f"Your post in {community.name} is now visible.", category=Notification.COMMUNITY)
                    messages.success(request, "Post approved and published.")
                else:
                    post.approval_status = CommunityPost.APPROVAL_REJECTED
                    post.rejection_reason = request.POST.get("reason", "").strip()
                    post.save(update_fields=["approval_status", "rejection_reason"])
                    _notify(post.author_user, "Post needs changes", f"Your post in {community.name} was not approved yet.", category=Notification.COMMUNITY)
                    messages.success(request, "Post declined. The author has been notified.")
            return HttpResponseRedirect(posts_url)

        elif action == "review_community_report":
            if not _community_has_permission(user, community, "moderate_content"):
                messages.error(request, "Only community owners and moderators can review reports.")
            else:
                report = get_object_or_404(ContentReport, pk=request.POST.get("report_id"), community=community)
                status = request.POST.get("status", ContentReport.IN_REVIEW)
                if status not in {ContentReport.IN_REVIEW, ContentReport.RESOLVED, ContentReport.DISMISSED, ContentReport.ESCALATED}:
                    status = ContentReport.IN_REVIEW
                notes = request.POST.get("resolution_notes", "").strip()
                report.status = status
                report.resolution_notes = notes
                report.reviewed_by = user
                report.save(update_fields=["status", "resolution_notes", "reviewed_by", "updated_at"])
                if report.post:
                    if status == ContentReport.ESCALATED:
                        report.post.safety_status = CommunityPost.REMOVED
                        report.post.removed_at = timezone.now()
                    elif status == ContentReport.RESOLVED:
                        report.post.safety_status = CommunityPost.FLAGGED
                    elif status == ContentReport.DISMISSED:
                        report.post.safety_status = CommunityPost.CLEAN
                        report.post.removed_at = None
                    report.post.moderation_notes = notes
                    report.post.save(update_fields=["safety_status", "removed_at", "moderation_notes"])
                _record_audit(user, "Reviewed community content report", target=f"Report #{report.id} in {community.name}", severity=AuditLog.WARNING)
                messages.success(request, "Moderation action saved.")
            return HttpResponseRedirect(f"{community_url}?tab=moderation")

        elif action == "remove_member" and can_manage_members:
            member_id = request.POST.get("member_id")
            member_to_remove = CommunityMembership.objects.filter(community=community, id=member_id).exclude(role=CommunityMembership.OWNER).first()
            if member_to_remove:
                member_to_remove.delete()
                _record_audit(user, "Removed member", target=member_to_remove.user.username)
                messages.success(request, f"Removed {member_to_remove.user.username} from the community.")
            return HttpResponseRedirect(reverse("hub:community_detail", kwargs={"slug": community.slug}) + "?tab=manage")

        elif action == "remove_member":
            messages.error(request, "Only the community owner can remove community members.")
            return HttpResponseRedirect(f"{community_url}?tab=manage")

        elif action == "like_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            engagement, created = PostEngagement.objects.get_or_create(
                post=post,
                user=user,
                engagement_type=PostEngagement.LIKE,
            )
            if not created:
                engagement.delete()
            else:
                rep, _ = UserReputation.objects.get_or_create(user=post.author_user)
                rep.likes_received += 1
                rep.save(update_fields=["likes_received"])
                rep.add_xp(2)
            return HttpResponseRedirect(posts_url)

        elif action == "share_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            PostEngagement.objects.get_or_create(
                post=post,
                user=user,
                engagement_type=PostEngagement.SHARE,
            )
            messages.success(request, "Post shared inside your activity history.")
            if _is_ajax_request(request):
                return HttpResponse("Post shared.", status=200)
            return HttpResponseRedirect(posts_url)

        elif action == "bookmark_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            bookmark, created = BookmarkedPost.objects.get_or_create(post=post, user=user)
            if not created:
                bookmark.delete()
            return HttpResponseRedirect(posts_url)

        elif action == "vote_poll":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            poll = getattr(post, "poll", None)
            if not poll:
                messages.error(request, "This post has no poll.")
                return HttpResponseRedirect(posts_url)
            option_ids = request.POST.getlist("option")
            if not option_ids:
                messages.error(request, "Select an option to vote.")
                return HttpResponseRedirect(posts_url)
            if not poll.is_multiple_choice:
                option_ids = [option_ids[0]]
            for oid in option_ids:
                option = get_object_or_404(poll.options, pk=oid)
                PollVote.objects.get_or_create(option=option, user=user)
            messages.success(request, "Vote recorded.")
            return HttpResponseRedirect(posts_url)

        elif action == "award_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            name = request.POST.get("award_name", "Gold")
            icon = request.POST.get("award_icon", "fa-trophy")
            PostAward.objects.create(post=post, giver=user, name=name, icon=icon)
            _notify(post.author_user, "Award received", f"@{user.username} gave a {name} award on your post.", category=Notification.PAYMENT)
            messages.success(request, f"{name} award given!")
            return HttpResponseRedirect(posts_url)

        elif action == "toggle_pin":
            if not is_manager:
                return HttpResponseRedirect(posts_url)
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            post.is_pinned = not post.is_pinned
            post.save(update_fields=["is_pinned"])
            return HttpResponseRedirect(posts_url)

        elif action == "donate_post":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community, accept_donations=True), pk=request.POST.get("post_id"))
            if post.author_user_id == user.id:
                messages.error(request, "You cannot donate to your own post.")
                return HttpResponseRedirect(posts_url)
            try:
                amount = Decimal(request.POST.get("amount", "0"))
            except Exception:
                amount = 0
            if amount <= 0:
                messages.error(request, "Enter a valid donation amount.")
                return HttpResponseRedirect(posts_url)
            gateway = request.POST.get("gateway", "manual")
            if gateway not in dict(PostDonation.GATEWAY_CHOICES):
                messages.error(request, "Choose a valid payment method.")
                return HttpResponseRedirect(posts_url)
            message = request.POST.get("message", "").strip()
            donation = PostDonation.objects.create(
                post=post,
                donor=user,
                amount=amount,
                gateway=gateway,
                status=PostDonation.SUCCESS,
                message=message,
            )
            PaymentTransaction.objects.create(
                user=user,
                community=community,
                amount=amount,
                gateway=gateway,
                status=PaymentTransaction.SUCCESS,
                notes=f"Donation to post #{post.id} in {community.name}",
                reference_code=f"DON-{donation.id}",
            )
            _notify(
                post.author_user,
                "Donation received",
                f"{display_name} donated Rs. {amount} to your post.",
                category=Notification.PAYMENT,
            )
            messages.success(request, f"Donation of Rs. {amount} successful!")
            try:
                check_and_award(user)
            except Exception:
                logger.exception("Failed to check and award badges after donation.")
            return HttpResponseRedirect(posts_url)

        elif action == "like_comment":
            comment = get_object_or_404(PostComment.objects.select_related("post"), pk=request.POST.get("comment_id"))
            if comment.post.community_id != community.id:
                return HttpResponseRedirect(posts_url)
            engagement, created = CommentEngagement.objects.get_or_create(
                comment=comment,
                user=user,
                engagement_type=CommentEngagement.LIKE,
            )
            if not created:
                engagement.delete()
            return HttpResponseRedirect(posts_url)

        elif action == "react_comment":
            comment = get_object_or_404(PostComment.objects.select_related("post"), pk=request.POST.get("comment_id"))
            if comment.post.community_id != community.id:
                return HttpResponseRedirect(posts_url)
            reaction = request.POST.get("reaction", CommentEngagement.LIKE).strip().lower()
            if reaction not in dict(CommentEngagement.ENGAGEMENT_CHOICES):
                reaction = CommentEngagement.LIKE
            existing = CommentEngagement.objects.filter(comment=comment, user=user)
            if existing.filter(engagement_type=reaction).exists():
                existing.filter(engagement_type=reaction).delete()
            else:
                existing.exclude(engagement_type=reaction).delete()
                CommentEngagement.objects.create(comment=comment, user=user, engagement_type=reaction)
            return HttpResponseRedirect(posts_url)

        elif action == "edit_comment":
            comment = get_object_or_404(PostComment.objects.select_related("post"), pk=request.POST.get("comment_id"))
            if comment.post.community_id != community.id:
                return HttpResponseRedirect(posts_url)
            if comment.author_id != user.id and not is_manager:
                messages.error(request, "You cannot edit this comment.")
            else:
                body = request.POST.get("body", "").strip()
                attachment_file = request.FILES.get("attachment_file")
                attachment_url = request.POST.get("attachment_url", "").strip()
                if not body and not attachment_file and not attachment_url:
                    messages.error(request, "Update text, an image, or a video before saving.")
                else:
                    attachment_error = _post_attachment_error(attachment_file)
                    if attachment_error:
                        messages.error(request, attachment_error)
                    else:
                        comment.body = body
                        if attachment_file:
                            comment.attachment_file = attachment_file
                        if attachment_url:
                            comment.attachment_url = attachment_url
                        comment.save()
                        _record_audit(user, "Edited community comment", target=community.name)
                        messages.success(request, "Comment updated.")
                        return HttpResponseRedirect(posts_url)
            return HttpResponseRedirect(posts_url)

        elif action == "delete_comment":
            comment = get_object_or_404(PostComment.objects.select_related("post"), pk=request.POST.get("comment_id"))
            if comment.post.community_id != community.id:
                return HttpResponseRedirect(posts_url)
            if comment.author_id != user.id and not is_manager:
                messages.error(request, "You cannot delete this comment.")
            else:
                comment.delete()
                _record_audit(user, "Deleted community comment", target=community.name)
                messages.success(request, "Comment deleted.")
            return HttpResponseRedirect(posts_url)

        elif action == "create_comment":
            post = get_object_or_404(_visible_posts_for(user).filter(community=community), pk=request.POST.get("post_id"))
            attachment_error = _post_attachment_error(request.FILES.get("attachment_file"))
            if attachment_error:
                response = _ajax_error_response(request, attachment_error)
                if response:
                    return response
                return HttpResponseRedirect(posts_url)
            comment_form = PostCommentForm(request.POST, request.FILES)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.post = post
                comment.author = user
                try:
                    comment.save()
                except OSError:
                    logger.exception("Unable to store community reply attachment.")
                    response = _ajax_error_response(
                        request,
                        "Unable to store the attachment. Please try again.",
                        status=500,
                    )
                    if response:
                        return response
                    return HttpResponseRedirect(posts_url)
                _process_mentions(comment.body, user, comment=comment)
                rep, _ = UserReputation.objects.get_or_create(user=user)
                rep.add_xp(5)
                check_and_award(user)
                messages.success(request, "Reply added.")
                if _is_ajax_request(request):
                    return HttpResponse("Reply added.", status=200)
                return HttpResponseRedirect(posts_url)
            message = "Please write a reply or attach a file before submitting."
            messages.error(request, message)
            if _is_ajax_request(request):
                return HttpResponse(message, status=400)

        else:
            messages.error(request, "Unknown community action.")

    comment_queryset = PostComment.objects.select_related("author", "author__profile").annotate(
        liked_by_user=Exists(
            CommentEngagement.objects.filter(
                comment=OuterRef("pk"),
                user=user,
                engagement_type=CommentEngagement.LIKE,
            )
        ),
        like_count=Count(
            "engagements",
            filter=Q(engagements__engagement_type=CommentEngagement.LIKE),
            distinct=True,
        ),
        reaction_count=Count(
            "engagements",
            filter=~Q(engagements__engagement_type=CommentEngagement.LIKE),
            distinct=True,
        ),
    )
    posts = (
        CommunityPost.objects.select_related("author_user", "author_user__profile")
        .filter(community=community)
        .filter(
            Q(approval_status=CommunityPost.APPROVAL_APPROVED)
            | Q(author_user=user)
            | Q(approval_status=CommunityPost.APPROVAL_PENDING, community__memberships__user=user, community__memberships__role__in=[CommunityMembership.OWNER, CommunityMembership.MODERATOR])
        )
        .exclude(safety_status=CommunityPost.REMOVED)
        .exclude(
            Exists(
                HiddenPost.objects.filter(
                    post=OuterRef("pk"),
                    user=user,
                )
            )
        )
        .annotate(
            liked_by_user=Exists(
                PostEngagement.objects.filter(
                    post=OuterRef("pk"),
                    user=user,
                    engagement_type=PostEngagement.LIKE,
                )
            ),
            like_count=Count("engagements", filter=Q(engagements__engagement_type=PostEngagement.LIKE), distinct=True),
            comment_count=Count("comments", distinct=True),
            share_count=Count("engagements", filter=Q(engagements__engagement_type=PostEngagement.SHARE), distinct=True),
        )
        .prefetch_related(Prefetch("comments", queryset=comment_queryset))
        .order_by("-is_pinned", "-created_at")
    )
    course_posts = posts.filter(
        Q(category__iexact="Course")
        | Q(content_type__in=[CommunityPost.ARTICLE, CommunityPost.VIDEO])
    )
    moderation_queue = CommunityPost.objects.none()
    community_reports = ContentReport.objects.none()
    if _community_has_permission(user, community, "moderate_content"):
        moderation_queue = (
            CommunityPost.objects.filter(
                community=community,
                approval_status=CommunityPost.APPROVAL_PENDING,
            )
            .select_related("author_user", "author_user__profile")
            .order_by("created_at")
        )
    # Ensure comment authors have a profile to avoid template errors when
    # accessing `author.profile` in templates (creates missing UserProfile rows).
    try:
        author_ids = set()
        for post in posts:
            for comment in getattr(post, "comments", []).all():
                if comment and getattr(comment, "author_id", None):
                    author_ids.add(comment.author_id)
        for uid in author_ids:
            UserProfile.objects.get_or_create(user_id=uid)
    except Exception:
        # Best-effort only; if anything goes wrong, don't break the view.
        pass
    members = CommunityMembership.objects.select_related("user", "user__profile").filter(community=community)
    member_by_user_id = {member.user_id: member for member in members}
    for post in posts:
        post.author_membership = member_by_user_id.get(post.author_user_id)
    for post in moderation_queue:
        post.author_membership = member_by_user_id.get(post.author_user_id)
    if _community_has_permission(user, community, "moderate_content"):
        community_reports = ContentReport.objects.filter(community=community).select_related(
            "reporter", "reported_user", "post", "reviewed_by"
        ).order_by("-ai_priority", "-created_at")[:50]
    events = community.events.select_related("organizer")[:6]

    payout_summary = _community_payout_summary(community) if can_request_payout else {
        "lifetime_total": Decimal("0.00"),
        "paid_total": Decimal("0.00"),
        "held_total": Decimal("0.00"),
        "available_total": Decimal("0.00"),
        "minimum": PAYOUT_MINIMUM,
        "can_request": False,
    }
    payout_requests = PayoutRequest.objects.none()
    if can_view_payments:
        payout_requests = PayoutRequest.objects.filter(community=community).select_related("requested_by", "processed_by")[:25]
        if can_request_payout and not payout_form.is_bound and payout_summary["can_request"]:
            payout_form.initial = {"amount": payout_summary["available_total"]}

    creator_insights = None
    if can_view_payments:
        total_members = members.count()
        total_posts = posts.count()
        membership_revenue = (
            PaymentTransaction.objects.filter(
                community=community,
                status=PaymentTransaction.SUCCESS,
            ).exclude(reference_code__startswith="DON-").aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        donation_revenue = (
            PostDonation.objects.filter(post__community=community, status=PostDonation.SUCCESS)
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        total_revenue = payout_summary["lifetime_total"]
        recent_members = members.order_by("-joined_at")[:5]
        membership_share = int((membership_revenue / total_revenue) * 100) if total_revenue else 0
        creator_insights = {
            "total_members": total_members,
            "total_posts": total_posts,
            "total_revenue": total_revenue,
            "membership_revenue": membership_revenue,
            "donation_revenue": donation_revenue,
            "membership_share": membership_share,
            "donation_share": 100 - membership_share if total_revenue else 0,
            "recent_members": recent_members,
        }

    dashboard_activity = []
    dashboard_summary = {"new_members": 0, "new_posts": 0, "seven_day_revenue": Decimal("0.00")}
    if active_community_tab in {"dashboard", "payments", "earnings"}:
        today = timezone.localdate()
        activity_posts = CommunityPost.objects.filter(community=community).exclude(safety_status=CommunityPost.REMOVED)
        max_posts = 0
        max_members = 0
        max_revenue = Decimal("0.00")
        for day_offset in range(6, -1, -1):
            activity_day = today - timedelta(days=day_offset)
            day_posts = activity_posts.filter(created_at__date=activity_day).count()
            day_members = members.filter(joined_at__date=activity_day).count()
            day_revenue = Decimal("0.00")
            if can_view_payments:
                day_revenue += PaymentTransaction.objects.filter(
                    status=PaymentTransaction.SUCCESS,
                    notes__icontains=f"Community join payment for {community.name}",
                    created_at__date=activity_day,
                ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
                day_revenue += PostDonation.objects.filter(
                    post__community=community,
                    status=PostDonation.SUCCESS,
                    created_at__date=activity_day,
                ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            max_posts = max(max_posts, day_posts)
            max_members = max(max_members, day_members)
            max_revenue = max(max_revenue, day_revenue)
            dashboard_activity.append({
                "label": activity_day.strftime("%a"),
                "date_label": f"{activity_day.strftime('%b')} {activity_day.day}",
                "posts": day_posts,
                "members": day_members,
                "revenue": day_revenue,
            })
        for activity_day in dashboard_activity:
            activity_day["posts_height"] = max(10, int((activity_day["posts"] / max_posts) * 100)) if max_posts else 8
            activity_day["members_height"] = max(10, int((activity_day["members"] / max_members) * 100)) if max_members else 8
            activity_day["revenue_height"] = max(10, int((activity_day["revenue"] / max_revenue) * 100)) if max_revenue else 8
        dashboard_summary = {
            "new_members": sum(item["members"] for item in dashboard_activity),
            "new_posts": sum(item["posts"] for item in dashboard_activity),
            "seven_day_revenue": sum((item["revenue"] for item in dashboard_activity), Decimal("0.00")),
        }

    user_communities = _community_cards(
        Community.objects.filter(memberships__user=user, is_archived=False).exclude(slug="").order_by("name")
    )
    active_subscription = _active_subscription_for(user)
    recent_community_posts = posts.order_by("-created_at")[:3]
    community_rooms = community.rooms.all().order_by("display_order", "name")
    community_links = community.links.all().order_by("display_order", "title", "url")
    community_announcements = Announcement.objects.filter(community=community, is_active=True).order_by("-is_pinned", "-created_at")
    community_payment_transactions = PaymentTransaction.objects.none()
    community_donations = PostDonation.objects.none()
    community_earnings_history = []
    if can_view_payments and active_community_tab in {"payments", "earnings"}:
        community_payment_transactions = PaymentTransaction.objects.filter(
            community=community,
            status=PaymentTransaction.SUCCESS,
        ).exclude(reference_code__startswith="DON-").select_related("user", "plan").order_by("-created_at")[:12]
        community_donations = PostDonation.objects.filter(
            post__community=community,
            status=PostDonation.SUCCESS,
        ).select_related("post", "donor").order_by("-created_at")[:12]
        for payment in community_payment_transactions:
            community_earnings_history.append({
                "kind": "Membership payment",
                "description": payment.plan.name if payment.plan else "Community join",
                "person": payment.user.get_full_name() or payment.user.username,
                "gateway": payment.get_gateway_display(),
                "amount": payment.amount,
                "created_at": payment.created_at,
                "icon": "fa-receipt",
                "tone": "sky",
            })
        for donation in community_donations:
            community_earnings_history.append({
                "kind": "Post donation",
                "description": donation.post.title or "Community post",
                "person": donation.donor.get_full_name() or donation.donor.username,
                "gateway": donation.get_gateway_display(),
                "amount": donation.amount,
                "created_at": donation.created_at,
                "icon": "fa-hand-holding-heart",
                "tone": "amber",
            })
        community_earnings_history.sort(key=lambda item: item["created_at"], reverse=True)

    leaderboard_entries = (
        CommunityMembership.objects.filter(community=community)
        .select_related("user", "user__profile", "user__reputation")
        .annotate(
            xp_total=Coalesce("user__reputation__xp", 0, output_field=IntegerField()),
            likes_total=Coalesce("user__reputation__likes_received", 0, output_field=IntegerField()),
        )
        .order_by("-xp_total", "joined_at")[:20]
    )

    # Handle room view
    active_room = None
    room_id = request.GET.get("room")
    if room_id:
        try:
            active_room = CommunityRoom.objects.get(pk=room_id, community=community)
        except (CommunityRoom.DoesNotExist, ValueError):
            active_room = None

    return render(
        request,
        "user-dashboard/community_detail.html",
        {
            "active_page": "communities",
            "user_dashboard_sections": _user_dashboard_sections(),
            "display_name": display_name,
            "profile": profile,
            "studio_started": bool(request.session.get("studio_started")) or _dashboard_onboarding_started(profile, "studio"),
            "today": timezone.localdate(),
            "community": community,
            "community_form": community_form,
            "member_count": members.count(),
            "post_count": posts.count(),
            "members": members,
            "posts": posts,
            "moderation_queue": moderation_queue,
            "community_reports": community_reports,
            "course_posts": course_posts,
            "events": events,
            "active_community_tab": active_community_tab,
            "compose_mode": compose_mode,
            "editing_post": editing_post,
            "current_user_id": user.id,
            "bookmarked_post_ids": list(BookmarkedPost.objects.filter(user=user).values_list("post_id", flat=True)),
            "bookmarked_posts": BookmarkedPost.objects.filter(user=user, post__community=community).select_related("post__author_user", "post__community").order_by("-created_at")[:50],
            "is_member": is_member,
            "is_owner": is_owner,
            "community_role": role,
            "is_manager": is_manager,
            "can_edit_community": can_edit_community,
            "can_manage_members": can_manage_members,
            "can_manage_roles": can_manage_roles,
            "can_view_payments": can_view_payments,
            "can_request_payout": can_request_payout,
            "membership": membership,
            "comment_engagement_choices": CommentEngagement.ENGAGEMENT_CHOICES,
            "creator_insights": creator_insights,
            "dashboard_activity": dashboard_activity,
            "dashboard_summary": dashboard_summary,
            "session_info": _session_context(request),
            "user_communities": user_communities,
            "active_subscription": active_subscription,
            "recent_posts": recent_community_posts,
            "community_rooms": community_rooms,
            "community_links": community_links,
            "community_announcements": community_announcements,
            "community_payment_transactions": community_payment_transactions,
            "community_donations": community_donations,
            "community_earnings_history": community_earnings_history,
            "payout_summary": payout_summary,
            "payout_requests": payout_requests,
            "payout_form": payout_form,
            "payout_minimum": PAYOUT_MINIMUM,
            "active_room": active_room,
            "leaderboard_entries": leaderboard_entries,
            "notification_count": _unread_notification_count(user),
            "unread_message_count": DirectMessage.objects.filter(thread__in=DirectMessageThread.objects.filter(Q(participant1=user) | Q(participant2=user)), is_read=False).exclude(sender=user).count(),
            "notifications": _notifications_for_user(user),
            "user_following": get_user_model().objects.filter(followers__follower=user, followers__status=UserFollow.ACCEPTED).select_related("profile")[:50],
            "friend_requests_received": get_user_model().objects.filter(following__followed=user, following__status=UserFollow.PENDING).select_related("profile")[:20],
            "friend_requests_sent": get_user_model().objects.filter(followers__follower=user, followers__status=UserFollow.PENDING).select_related("profile")[:20],
            "suggested_users": get_user_model().objects.exclude(pk=user.pk).exclude(is_superuser=True).select_related("profile")[:50],
            "dm_previews": _build_dm_previews(user),
        },
    )


def user_dashboard(request, section="overview"):
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    message_started = bool(request.session.get("messages_started")) or _dashboard_onboarding_started(profile, "messages")
    if section == "messages" and request.GET.get("started") == "1":
        request.session["messages_started"] = True
        _mark_dashboard_onboarding_started(profile, "messages")
        message_started = True
    discover_started = bool(request.session.get("discover_started")) or _dashboard_onboarding_started(profile, "discover")
    if section == "discover" and request.GET.get("started") == "1":
        request.session["discover_started"] = True
        _mark_dashboard_onboarding_started(profile, "discover")
        discover_started = True
    communities_started = bool(request.session.get("communities_started")) or _dashboard_onboarding_started(profile, "communities")
    if section == "communities" and request.GET.get("started") == "1":
        request.session["communities_started"] = True
        _mark_dashboard_onboarding_started(profile, "communities")
        communities_started = True
    studio_started = bool(request.session.get("studio_started")) or _dashboard_onboarding_started(profile, "studio")
    if section == "studio" and request.GET.get("started") == "1":
        request.session["studio_started"] = True
        _mark_dashboard_onboarding_started(profile, "studio")
        studio_started = True
    display_name = _display_name(user)
    settings_tabs = {"account", "personalization", "payments", "subscriptions"}
    settings_tab = request.GET.get("tab", "account") if section == "settings" else ""
    if settings_tab not in settings_tabs:
        settings_tab = "account"
    _ensure_default_subscription_plans()
    community_form = CommunityForm()
    post_form = CommunityPostForm(user=user, initial={"category": "General"})
    profile_form = UserProfileForm(instance=profile, user=user)
    personalization_form = PersonalizationForm(profile=profile)
    kyc_form = KYCVerificationForm(instance=profile, user=user)
    payment_settings_form = PaymentSettingsForm(instance=profile)
    payout_form = PayoutRequestForm()
    report_form = ContentReportForm()
    comment_form = PostCommentForm()

    if request.method == "POST":
        action = request.POST.get("action", "create_post")

        if action == "update_profile":
            profile_form = UserProfileForm(request.POST, request.FILES, instance=profile, user=user)
            if profile_form.is_valid():
                profile_form.save()
                _record_audit(user, "Updated profile", target=user.username)
                messages.success(request, "Your profile has been updated.")
                return HttpResponseRedirect(_dashboard_section_url("settings"))
            messages.error(request, "Please correct the profile form and try again.")

        elif action == "save_personalization":
            personalization_form = PersonalizationForm(request.POST, profile=profile)
            if personalization_form.is_valid():
                personalization_form.save()
                _record_audit(user, "Updated personalization preferences", target=user.username)
                messages.success(request, "Your personalization preferences have been saved.")
                return HttpResponseRedirect(_dashboard_section_url("personalization"))
            messages.error(request, "Please correct the personalization preferences and try again.")

        elif action == "update_profile_quick":
            from .forms import ProfileQuickEditForm
            quick_form = ProfileQuickEditForm(request.POST, request.FILES, instance=profile, user=user)
            if quick_form.is_valid():
                quick_form.save()
                _record_audit(user, "Updated profile (quick edit)", target=user.username)
                messages.success(request, "Your profile has been updated.")
            else:
                messages.error(request, "Please correct the profile form and try again.")
            referer = request.META.get("HTTP_REFERER")
            if referer and "settings" in referer:
                return HttpResponseRedirect(_dashboard_section_url("settings"))
            return HttpResponseRedirect(referer or _dashboard_section_url("overview"))

        elif action == "verify_kyc":
            kyc_form = KYCVerificationForm(request.POST, instance=profile, user=user)
            if kyc_form.is_valid():
                kyc_form.save()
                _record_audit(user, "Verified KYC", target=user.username)
                messages.success(request, "Your KYC verification has been completed. You can now access payments and reports.")
                return HttpResponseRedirect(_dashboard_section_url("settings"))
            messages.error(request, "Please correct the KYC form and try again.")

        elif action == "send_message":
            thread_id = request.POST.get("thread_id")
            body = request.POST.get("body", "").strip()
            attachment_file = request.FILES.get("attachment")
            attachment_error = ""
            if attachment_file:
                message_allowed_extensions = {
                    "jpg", "jpeg", "png", "gif", "webp", "mp4", "webm", "mov", "m4v",
                    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "zip",
                }
                extension = attachment_file.name.rsplit(".", 1)[-1].lower() if "." in attachment_file.name else ""
                if attachment_file.size > 50 * 1024 * 1024:
                    attachment_error = "Attachment must be 50 MB or smaller."
                elif extension not in message_allowed_extensions:
                    attachment_error = "Use an image, video, PDF, document, spreadsheet, presentation, text, or ZIP file."
            if attachment_error:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({"ok": False, "error": attachment_error}, status=400)
                messages.error(request, attachment_error)
            elif thread_id and (body or attachment_file):
                try:
                    thread = DirectMessageThread.objects.filter(
                        Q(id=thread_id) & (Q(participant1=user) | Q(participant2=user))
                    ).get()
                    DirectMessage.objects.create(
                        thread=thread,
                        sender=user,
                        body=body,
                        attachment=attachment_file,
                    )
                    thread.last_message_at = timezone.now()
                    thread.save(update_fields=["last_message_at"])
                except DirectMessageThread.DoesNotExist:
                    messages.error(request, "Message thread not found.")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({"ok": True})
            return HttpResponseRedirect(_dashboard_section_url("messages"))

        elif action == "start_message":
            User = get_user_model()
            recipient = User.objects.filter(pk=request.POST.get("user_id"), is_active=True).exclude(pk=user.pk).first()
            if recipient is None:
                messages.error(request, "Choose a valid user to message.")
            else:
                thread, _ = _direct_message_thread_for(user, recipient)
                messages.success(request, f"Conversation with {recipient.username} is ready.")
                if _is_ajax_request(request):
                    return HttpResponse(
                        json.dumps({"ok": True, "thread_id": thread.pk}),
                        content_type="application/json",
                    )
                return HttpResponseRedirect(f"{_dashboard_section_url('messages')}?thread={thread.pk}")
            if _is_ajax_request(request):
                return HttpResponse(json.dumps({"ok": False}), status=400, content_type="application/json")
            return HttpResponseRedirect(_dashboard_section_url("messages"))

        elif action == "save_payment_settings":
            payment_settings_form = PaymentSettingsForm(request.POST, instance=profile)
            if payment_settings_form.is_valid():
                payment_settings_form.save()
                messages.success(request, "Payment settings saved successfully.")
            else:
                messages.error(request, "Please correct the payment form.")
            if section == "settings":
                return HttpResponseRedirect(f"{_dashboard_section_url('settings')}?tab=payments#payout-settings")
            return HttpResponseRedirect(f"{_dashboard_section_url('payments')}#payout-settings")

        elif action == "request_creator_payout":
            payout_form = PayoutRequestForm(request.POST)
            if payout_form.is_valid():
                owned_ids = list(
                    Community.objects.filter(owner=user, is_archived=False).values_list("id", flat=True)
                )
                payout_summary = _creator_payout_summary(user, owned_ids)
                payout_amount = payout_form.cleaned_data["amount"]
                payout_method = _payout_method_for_profile(profile)
                if not profile.kyc_verified:
                    payout_form.add_error(None, "Complete identity verification before requesting a payout.")
                elif not payout_method:
                    payout_form.add_error(None, "Add a bank account, eSewa ID, or Khalti ID before requesting a payout.")
                elif payout_amount > payout_summary["available_total"]:
                    payout_form.add_error(
                        "amount",
                        f"You can request up to Rs. {payout_summary['available_total']:,.2f} across your communities.",
                    )
                else:
                    PayoutRequest.objects.create(
                        community=None,
                        requested_by=user,
                        amount=payout_amount,
                        payout_method=payout_method,
                        creator_note=payout_form.cleaned_data.get("note", "").strip(),
                    )
                    _record_audit(user, "Requested creator payout", target=user.username)
                    messages.success(request, "Your creator payout request has been submitted for review.")
                    return HttpResponseRedirect(f"{_dashboard_section_url('studio')}?started=1#payout-request")
            if payout_form.errors:
                messages.error(request, "Please review the payout request details.")

        elif action == "request_account_delete":
            profile.account_deletion_requested_at = timezone.now()
            profile.save(update_fields=["account_deletion_requested_at"])
            _record_audit(user, "Requested account deletion", target=user.username, severity=AuditLog.WARNING)
            messages.success(request, "Your account deletion request has been recorded.")
            return HttpResponseRedirect(_dashboard_section_url("settings"))

        elif action == "create_community":
            owned_count = Community.objects.filter(owner=user, is_archived=False).count()
            if owned_count >= _community_limit_for(user):
                messages.error(request, "Your current subscription limit has been reached.")
                return HttpResponseRedirect(_dashboard_section_url("subscriptions"))
            community_form = CommunityForm(request.POST, request.FILES)
            if community_form.is_valid():
                community = community_form.save(commit=False)
                community.owner = user
                community.save()
                CommunityMembership.objects.get_or_create(
                    community=community,
                    user=user,
                    defaults={"role": CommunityMembership.OWNER},
                )
                _record_audit(user, "Created community", target=community.name)
                messages.success(request, f"{community.name} has been created.")
                return HttpResponseRedirect(_dashboard_section_url("communities"))
            messages.error(request, "Please correct the community form and try again.")

        elif action == "join_community":
            community = Community.objects.filter(
                pk=request.POST.get("community_id"),
                visibility=Community.PUBLIC,
            ).first()
            if community is None:
                messages.error(request, "That community is not available to join.")
            elif CommunityMembership.objects.filter(community=community, user=user).exists():
                messages.success(request, f"You are already a member of {community.name}.")
            else:
                payment_redirect = _community_join_redirect(request, community)
                if payment_redirect:
                    return payment_redirect
                if community.requires_payment:
                    transaction = _record_community_join_payment(user, community)
                    _notify(
                        user,
                        "Payment confirmed",
                        f"Your Rs. {transaction.amount} payment for {community.name} was recorded.",
                        category=Notification.PAYMENT,
                    )
                CommunityMembership.objects.create(community=community, user=user)
                _notify(user, "Community joined", f"You joined {community.name}.", category=Notification.COMMUNITY)
                _record_audit(user, "Joined community", target=community.name)
                if community.requires_payment:
                    messages.success(request, f"Payment received. You joined {community.name}.")
                else:
                    messages.success(request, f"You joined {community.name}.")
            return HttpResponseRedirect(_dashboard_section_url("communities"))

        elif action == "leave_community":
            community = get_object_or_404(Community, pk=request.POST.get("community_id"))
            if community.owner == user:
                messages.error(request, "Owners cannot leave their own community. Delete it instead.")
            else:
                CommunityMembership.objects.filter(community=community, user=user).delete()
                _record_audit(user, "Left community", target=community.name)
                messages.success(request, f"You left {community.name}.")
            return HttpResponseRedirect(_dashboard_section_url("communities"))

        elif action == "delete_community":
            community = get_object_or_404(Community, pk=request.POST.get("community_id"), owner=user)
            community_name = community.name
            community.delete()
            _record_audit(user, "Deleted community", target=community_name, severity=AuditLog.WARNING)
            messages.success(request, f"{community_name} has been deleted.")
            return HttpResponseRedirect(_dashboard_section_url("communities"))

        elif action == "create_post":
            post_form = CommunityPostForm(request.POST, user=user)
            if post_form.is_valid():
                post = post_form.save(commit=False)
                post.author_user = user
                post.author = display_name
                if not post.ai_summary:
                    post.ai_summary = _generate_ai_summary(post.body)
                post.save()
                _record_audit(user, "Created post", target=post.title)
                messages.success(request, "Your post has been published.")
                return HttpResponseRedirect(_dashboard_section_url("overview"))
            messages.error(request, "Please correct the post form and try again.")

        elif action == "like_post":
            post = get_object_or_404(_visible_posts_for(user), pk=request.POST.get("post_id"))
            engagement, created = PostEngagement.objects.get_or_create(
                post=post,
                user=user,
                engagement_type=PostEngagement.LIKE,
            )
            if not created:
                engagement.delete()
            return HttpResponseRedirect(_dashboard_section_url("overview"))

        elif action == "share_post":
            post = get_object_or_404(_visible_posts_for(user), pk=request.POST.get("post_id"))
            PostEngagement.objects.get_or_create(
                post=post,
                user=user,
                engagement_type=PostEngagement.SHARE,
            )
            messages.success(request, "Post shared inside your activity history.")
            return HttpResponseRedirect(_dashboard_section_url("overview"))

        elif action == "bookmark_post":
            post = get_object_or_404(_visible_posts_for(user), pk=request.POST.get("post_id"))
            bookmark, created = BookmarkedPost.objects.get_or_create(post=post, user=user)
            if not created:
                bookmark.delete()
            return HttpResponseRedirect(_dashboard_section_url("overview"))

        elif action == "pin_post":
            post = get_object_or_404(CommunityPost, pk=request.POST.get("post_id"), author_user=user)
            post.is_pinned = not post.is_pinned
            post.save(update_fields=["is_pinned"])
            return HttpResponseRedirect(_dashboard_section_url("overview"))

        elif action == "create_comment":
            post = get_object_or_404(_visible_posts_for(user), pk=request.POST.get("post_id"))
            comment_form = PostCommentForm(request.POST)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.post = post
                comment.author = user
                comment.save()
                messages.success(request, "Comment added.")
                return HttpResponseRedirect(_dashboard_section_url("overview"))
            messages.error(request, "Please write a comment before submitting.")

        elif action == "report_post":
            if not profile.kyc_verified:
                messages.error(request, "Please complete KYC verification to submit reports. Go to Settings > KYC Verification.")
                return HttpResponseRedirect(_dashboard_section_url("overview"))
            post = get_object_or_404(_visible_posts_for(user), pk=request.POST.get("post_id"))
            report_form = ContentReportForm(request.POST)
            if report_form.is_valid():
                report = report_form.save(commit=False)
                report.reporter = user
                report.reported_user = post.author_user
                report.community = post.community
                report.post = post
                report.ai_category, report.ai_priority = _classify_report(report.reason, report.description)
                report.save()
                post.safety_status = CommunityPost.FLAGGED
                post.save(update_fields=["safety_status"])
                _record_audit(user, "Submitted content report", target=post.title, severity=AuditLog.WARNING)
                messages.success(request, "Report submitted for moderation review.")
                return HttpResponseRedirect(_dashboard_section_url("reports"))
            messages.error(request, "Please correct the report form and try again.")

        elif action == "subscribe_plan":
            plan = get_object_or_404(SubscriptionPlan, pk=request.POST.get("plan_id"), is_active=True)
            gateway = request.POST.get("gateway", PaymentTransaction.MANUAL)
            if gateway not in dict(PaymentTransaction.GATEWAY_CHOICES):
                gateway = PaymentTransaction.MANUAL
            if gateway == PaymentTransaction.ESEWA:
                return esewa_subscription_payment_start(request, plan.pk)

            transaction = _record_local_subscription_payment(user, plan)
            messages.success(request, f"{plan.name} subscription is active.")
            return HttpResponseRedirect(_dashboard_section_url("subscriptions"))

        elif action == "dismiss_notification":
            Notification.objects.filter(pk=request.POST.get("notification_id"), user=user).update(is_read=True)
            return HttpResponseRedirect(_dashboard_section_url("notifications"))

        elif action == "cancel_subscription":
            sub_id = request.POST.get("subscription_id")
            UserSubscription.objects.filter(pk=sub_id, user=user, status=UserSubscription.ACTIVE).update(
                status=UserSubscription.CANCELLED
            )
            _record_audit(user, "Cancelled subscription")
            messages.success(request, "Your subscription has been cancelled.")
            if section == "settings":
                return HttpResponseRedirect(f"{_dashboard_section_url('settings')}?tab=subscriptions")
            return HttpResponseRedirect(_dashboard_section_url("subscriptions"))

        else:
            messages.error(request, "Unknown dashboard action.")

    user_communities = _community_cards(
        Community.objects.filter(memberships__user=user, is_archived=False).exclude(slug="").order_by("name")
    )
    discover_search = request.GET.get("q", "").strip()
    discover_category = request.GET.get("category", "").strip()
    discover_qs = Community.objects.filter(visibility=Community.PUBLIC, is_archived=False)
    if discover_search:
        discover_qs = discover_qs.filter(
            Q(name__icontains=discover_search)
            | Q(description__icontains=discover_search)
            | Q(topic__icontains=discover_search)
            | Q(location__icontains=discover_search)
            | Q(tags__icontains=discover_search)
        )
    if discover_category and discover_category != "Explore":
        discover_qs = discover_qs.filter(topic__iexact=discover_category)
    discover_categories = (
        Community.objects.filter(visibility=Community.PUBLIC, is_archived=False)
        .values_list("topic", flat=True)
        .distinct()
    )
    trending_since = timezone.now() - timedelta(days=30)
    discover_communities = list(
        _community_cards(
            discover_qs.annotate(member_count_raw=Count("memberships", distinct=True))
            .annotate(
                recent_post_count=Count(
                    "posts",
                    filter=Q(posts__created_at__gte=trending_since),
                    distinct=True,
                ),
                recent_member_count=Count(
                    "memberships",
                    filter=Q(memberships__joined_at__gte=trending_since) & ~Q(memberships__user=user),
                    distinct=True,
                ),
                recent_like_count=Count(
                    "posts__engagements",
                    filter=Q(
                        posts__engagements__created_at__gte=trending_since,
                        posts__engagements__engagement_type=PostEngagement.LIKE,
                    ),
                    distinct=True,
                ),
                recent_share_count=Count(
                    "posts__engagements",
                    filter=Q(
                        posts__engagements__created_at__gte=trending_since,
                        posts__engagements__engagement_type=PostEngagement.SHARE,
                    ),
                    distinct=True,
                ),
                recent_comment_count=Count(
                    "posts__comments",
                    filter=Q(posts__comments__created_at__gte=trending_since),
                    distinct=True,
                ),
            )
            .select_related("owner")
            .order_by("name")
        )
    )
    for community in discover_communities:
        community.recent_engagement_count = (
            community.recent_like_count
            + community.recent_share_count
            + community.recent_comment_count
        )
        # A fresh, explainable score: new members and active conversations weigh
        # more than lifetime size, so smaller communities can trend too.
        community.trending_score = (
            (community.recent_member_count * 5)
            + (community.recent_post_count * 4)
            + (community.recent_engagement_count * 2)
            + min(community.member_count_raw, 100) // 10
        )
        if community.recent_member_count or community.recent_post_count or community.recent_engagement_count:
            community.trending_reason = (
                f"{community.recent_post_count} recent posts · "
                f"{community.recent_engagement_count} interactions"
            )
        else:
            community.trending_reason = "Ready to be discovered"
    discover_communities.sort(key=lambda community: (-community.trending_score, community.name.lower()))
    trending_communities = discover_communities[:5]
    discover_other_communities = discover_communities[5:]
    discover_joined_ids = set(
        CommunityMembership.objects.filter(user=user, community__in=discover_communities).values_list("community_id", flat=True)
    )
    for index, community in enumerate(discover_communities):
        community.is_owner = community.owner_id == user.id
        community.is_member = community.is_owner or community.pk in discover_joined_ids
        community.cover_style = COMMUNITY_CARD_COVERS[index % len(COMMUNITY_CARD_COVERS)]
    recent_posts = (
        _visible_posts_for(user)
        .annotate(
            like_count=Count("engagements", filter=Q(engagements__engagement_type=PostEngagement.LIKE), distinct=True),
            comment_count=Count("comments", distinct=True),
            share_count=Count("engagements", filter=Q(engagements__engagement_type=PostEngagement.SHARE), distinct=True),
        )
        .order_by("-is_pinned", "-created_at")[:8]
    )
    reports = ContentReport.objects.select_related("post", "community").filter(reporter=user)[:6]
    notifications = _notifications_for_user(user)
    notification_total_count = notifications.count()
    notification_archived_count = notifications.filter(is_archived=True).count()
    notification_read_count = notifications.filter(is_read=True, is_archived=False).count()
    notification_categories = [
        {"key": Notification.COMMUNITY, "label": "Community", "icon": "fa-people-group", "tone": "violet", "count": notifications.filter(category=Notification.COMMUNITY).count()},
        {"key": Notification.PAYMENT, "label": "Payments", "icon": "fa-wallet", "tone": "emerald", "count": notifications.filter(category=Notification.PAYMENT).count()},
        {"key": Notification.MODERATION, "label": "Moderation", "icon": "fa-shield-halved", "tone": "amber", "count": notifications.filter(category=Notification.MODERATION).count()},
        {"key": Notification.ACCOUNT, "label": "Account", "icon": "fa-user-gear", "tone": "sky", "count": notifications.filter(category=Notification.ACCOUNT).count()},
    ]
    transactions = PaymentTransaction.objects.select_related("plan", "community").filter(user=user)[:8]
    message_threads = DirectMessageThread.objects.filter(
        (Q(participant1=user) | Q(participant2=user)),
        status=DirectMessageThread.ACTIVE,
    ).prefetch_related("messages").order_by("-last_message_at")[:20]
    message_archived_threads = DirectMessageThread.objects.filter(
        (Q(participant1=user) | Q(participant2=user)),
        status=DirectMessageThread.ARCHIVED,
    ).prefetch_related("messages").order_by("-last_message_at")[:20]
    plans = list(SubscriptionPlan.objects.filter(is_active=True))
    for p in plans:
        p.feature_list = [f.strip() for f in p.feature_summary.splitlines() if f.strip()]
    active_subscription = _active_subscription_for(user)
    community_tab = _community_tab_from_request(request)
    community_search = request.GET.get("q", "").strip()
    community_category = request.GET.get("category", "").strip()
    community_sort = request.GET.get("sort", "popular").strip()
    if community_sort not in {"popular", "recent", "name"}:
        community_sort = "popular"
    community_cards = _community_listing_for_user(
        user,
        tab=community_tab,
        search=community_search,
        category=community_category,
        sort=community_sort,
    )
    joined_communities_count = Community.objects.filter(memberships__user=user, is_archived=False).distinct().count()
    managed_communities_count = Community.objects.filter(owner=user, is_archived=False).count()
    member_upcoming_events = (
        CommunityEvent.objects.select_related("community", "organizer")
        .filter(community__memberships__user=user, starts_at__gte=timezone.now())
        .order_by("starts_at")[:6]
    )
    top_communities = list(
        Community.objects.filter(is_archived=False)
        .annotate(member_count_raw=Count("memberships", distinct=True))
        .exclude(memberships__user=user)
        .order_by("-member_count_raw", "name")[:6]
    )
    for index, community in enumerate(top_communities):
        community.cover_style = COMMUNITY_CARD_COVERS[index % len(COMMUNITY_CARD_COVERS)]
    message_user_search = request.GET.get("user_q", "").strip()
    message_user_results = _message_user_results(user, message_user_search) if section == "messages" else []
    open_message_thread_id = request.GET.get("thread", "").strip() if section == "messages" else ""
    creator_context = _creator_studio_context(
        user,
        selected_community_slug=request.GET.get("community", "").strip() if section == "payments" else "",
    )
    if section == "studio" and payout_form.is_bound:
        creator_context["creator_payout_form"] = payout_form

    # Onboarding checklist context
    has_community = Community.objects.filter(owner=user, is_archived=False).exists()
    user_first_community = user_communities.first() if user_communities else None
    has_members = CommunityMembership.objects.filter(
        community__owner=user
    ).exclude(role=CommunityMembership.OWNER).exists() if has_community else False
    has_branding = bool(user_first_community and (user_first_community.cover_image or user_first_community.tags))
    has_post = CommunityPost.objects.filter(author_user=user).exists()
    profile_completed = bool(profile.profile_image or (user.first_name and user.last_name))

    tasks = [
        profile_completed,
        has_community,
        has_members,
        has_branding,
        has_post,
    ]
    completed_tasks = sum(tasks)
    total_tasks = len(tasks)
    progress_percent = int((completed_tasks / total_tasks) * 100) if total_tasks else 0

    checklist_context = {
        "completed_tasks": completed_tasks,
        "total_tasks": total_tasks,
        "progress_percent": progress_percent,
        "has_community": has_community,
        "has_members": has_members,
        "has_branding": has_branding,
        "has_post": has_post,
        "profile_completed": profile_completed,
        "community_setup_completed": has_community and has_members and has_branding and has_post,
        "total_members": CommunityMembership.objects.filter(community__owner=user).count(),
        "total_posts": CommunityPost.objects.filter(author_user=user).count(),
        "owned_community_count": Community.objects.filter(owner=user, is_archived=False).count(),
        "total_revenue": int((
            PaymentTransaction.objects.filter(
                status=PaymentTransaction.SUCCESS, user=user
            ).aggregate(total=Sum("amount"))["total"] or 0)
        ),
    }

    if section == "overview":
        section_template = "user-dashboard/sections/dashboard_content.html"
    else:
        section_template = f"user-dashboard/sections/{section}.html"

    bookmarked_ids = list(BookmarkedPost.objects.filter(user=user).values_list("post_id", flat=True))
    bookmarked_posts = BookmarkedPost.objects.filter(user=user).select_related("post__author_user", "post__community").order_by("-created_at")[:50]

    UserModel = get_user_model()
    suggested_users = UserModel.objects.exclude(pk=user.pk).exclude(is_superuser=True).select_related("profile")[:50]
    user_following = UserModel.objects.filter(followers__follower=user, followers__status=UserFollow.ACCEPTED).select_related("profile")[:50]
    friend_requests_received = UserModel.objects.filter(following__followed=user, following__status=UserFollow.PENDING).select_related("profile")[:20]
    friend_requests_sent = UserModel.objects.filter(followers__follower=user, followers__status=UserFollow.PENDING).select_related("profile")[:20]

    return render(
        request,
        "user-dashboard/sections/dashboard.html",
        {
            "bookmarked_post_ids": bookmarked_ids,
            "bookmarked_posts": bookmarked_posts,
            "display_name": display_name,
            "active_page": section,
            "settings_tab": settings_tab,
            "section_template": section_template,
            "profile": profile,
            "today": timezone.localdate(),
            "notification_count": _unread_notification_count(user),
            "unread_message_count": DirectMessage.objects.filter(thread__in=DirectMessageThread.objects.filter(Q(participant1=user) | Q(participant2=user)), is_read=False).exclude(sender=user).count(),
            "stats": _user_dashboard_stats(user),
            "insights": _community_insights(),
            "community_form": community_form,
            "post_form": post_form,
            "profile_form": profile_form,
            "personalization_form": personalization_form,
            "kyc_form": kyc_form,
            "payment_settings_form": payment_settings_form,
            "report_form": report_form,
            "comment_form": comment_form,
            "user_communities": user_communities,
            "discover_communities": discover_communities,
            "trending_communities": trending_communities,
            "discover_other_communities": discover_other_communities,
            "discover_search": discover_search,
            "discover_category": discover_category,
            "discover_started": discover_started,
            "communities_started": communities_started,
            "studio_started": studio_started,
            "discover_categories": discover_categories,
            "community_cards": community_cards,
            "community_tab": community_tab,
            "community_search": community_search,
            "community_category": community_category,
            "community_sort": community_sort,
            "joined_communities_count": joined_communities_count,
            "managed_communities_count": managed_communities_count,
            "member_upcoming_events": member_upcoming_events,
            "top_communities": top_communities,
            "feed_posts": CommunityPost.objects.filter(
                author_user__in=UserFollow.objects.filter(follower=user).values("followed")
            ).select_related("author_user", "community").order_by("-created_at")[:10],
            "recent_posts": recent_posts,
            "reports": reports,
            "notifications": notifications,
            "notification_total_count": notification_total_count,
            "notification_archived_count": notification_archived_count,
            "notification_read_count": notification_read_count,
            "notification_categories": notification_categories,
            "message_threads": message_threads,
            "message_archived_threads": message_archived_threads,
            "message_started": message_started,
            "message_requests": friend_requests_received,
            "message_user_search": message_user_search,
            "message_user_results": message_user_results,
            "open_message_thread_id": open_message_thread_id,
            "transactions": transactions,
            "can_view_platform_finance": _is_platform_owner(request.user),
            "esewa_enabled": esewa_is_configured(),
            "plans": plans,
            "active_subscription": active_subscription,
            "community_limit": _community_limit_for(user),
            "session_info": _session_context(request),
            "user_dashboard_sections": _user_dashboard_sections(),
            "suggested_users": suggested_users,
            "user_following": user_following,
            "friend_requests_received": friend_requests_received,
            "friend_requests_sent": friend_requests_sent,
            "dm_previews": _build_dm_previews(user),
            **creator_context,
            **checklist_context,
        },
    )


@login_required
def super_admin_dashboard(request, section="overview"):
    if section not in SUPER_ADMIN_SECTION_KEYS:
        section = "overview"

    # Subscription plans, gateway activity, transactions, and revenue are
    # financial controls. They are deliberately exclusive to the Owner.
    if section == "subscriptions" and not _is_platform_owner(request.user):
        messages.error(request, "Only the platform owner can access subscription and financial management.")
        return redirect("hub:dashboard_section", section="overview")

    user_form = AdminUserCreationForm(initial={"is_active": True})
    community_form = AdminCommunityForm()
    post_form = AdminCommunityPostForm(initial={"category": "General"})
    plan_form = SubscriptionPlanForm(initial={"is_active": True})
    _ensure_default_subscription_plans()

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create_user":
            user_form = AdminUserCreationForm(request.POST)
            if user_form.is_valid():
                new_user = user_form.save()
                requested_role = user_form.cleaned_data.get("platform_role") or UserProfile.PLATFORM_MEMBER
                if requested_role not in _manageable_platform_roles(request.user):
                    requested_role = UserProfile.PLATFORM_MEMBER
                new_user.is_superuser = requested_role == UserProfile.PLATFORM_OWNER and _is_platform_owner(request.user)
                new_user.is_staff = requested_role in {UserProfile.PLATFORM_OWNER, UserProfile.PLATFORM_ADMIN}
                new_user.save(update_fields=["is_superuser", "is_staff"])
                UserProfile.objects.update_or_create(
                    user=new_user,
                    defaults={"platform_role": requested_role},
                )
                _record_audit(request.user, "Created user", target=new_user.username)
                messages.success(request, f"{_display_name(new_user)} has been created.")
                return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'users'}))
            messages.error(request, "Please correct the user form and try again.")

        elif action == "toggle_user_status":
            managed_user = get_object_or_404(get_user_model(), pk=request.POST.get("user_id"))
            if managed_user == request.user:
                messages.error(request, "You cannot disable your own super admin account.")
            else:
                managed_user.is_active = not managed_user.is_active
                managed_user.save(update_fields=["is_active"])
                state = "activated" if managed_user.is_active else "disabled"
                _record_audit(request.user, f"User {state}", target=managed_user.username, severity=AuditLog.WARNING)
                messages.success(request, f"{_display_name(managed_user)} has been {state}.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'users'}))

        elif action == "update_user_role":
            managed_user = get_object_or_404(get_user_model(), pk=request.POST.get("user_id"))
            if managed_user.is_superuser:
                messages.error(request, "The platform owner role is protected.")
            else:
                managed_user.is_active = request.POST.get("is_active") == "on"
                update_fields = ["is_active"]
                if _is_platform_admin(request.user):
                    requested_role = request.POST.get("platform_role", UserProfile.PLATFORM_MEMBER)
                    if requested_role not in _manageable_platform_roles(request.user):
                        requested_role = UserProfile.PLATFORM_MEMBER
                    managed_profile, _ = UserProfile.objects.get_or_create(user=managed_user)
                    managed_profile.platform_role = requested_role
                    managed_profile.save(update_fields=["platform_role"])
                    managed_user.is_superuser = requested_role == UserProfile.PLATFORM_OWNER
                    managed_user.is_staff = requested_role in {UserProfile.PLATFORM_OWNER, UserProfile.PLATFORM_ADMIN}
                    update_fields.extend(["is_superuser", "is_staff"])
                managed_user.save(update_fields=update_fields)
                _record_audit(request.user, "Updated user privileges", target=managed_user.username)
                messages.success(request, f"{_display_name(managed_user)} privileges updated.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'users'}))

        elif action == "delete_user":
            managed_user = get_object_or_404(get_user_model(), pk=request.POST.get("user_id"))
            if managed_user == request.user or managed_user.is_superuser:
                messages.error(request, "This account cannot be removed from the dashboard.")
            else:
                target = managed_user.username
                managed_user.delete()
                _record_audit(request.user, "Deleted user", target=target, severity=AuditLog.CRITICAL)
                messages.success(request, f"{target} has been removed.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'users'}))

        elif action == "create_community":
            community_form = AdminCommunityForm(request.POST, request.FILES)
            if community_form.is_valid():
                community = community_form.save()
                CommunityMembership.objects.get_or_create(
                    community=community,
                    user=community.owner,
                    defaults={"role": CommunityMembership.OWNER},
                )
                _record_audit(request.user, "Created community", target=community.name)
                messages.success(request, f"{community.name} has been created.")
                return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'communities'}))
            messages.error(request, "Please correct the community form and try again.")

        elif action == "delete_community":
            community = get_object_or_404(Community, pk=request.POST.get("community_id"))
            community_name = community.name
            community.delete()
            _record_audit(request.user, "Deleted community", target=community_name, severity=AuditLog.WARNING)
            messages.success(request, f"{community_name} has been deleted.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'communities'}))

        elif action == "toggle_community_archived":
            community = get_object_or_404(Community, pk=request.POST.get("community_id"))
            community.is_archived = not community.is_archived
            community.save(update_fields=["is_archived"])
            state = "restored" if not community.is_archived else "archived"
            _record_audit(request.user, f"{state.capitalize()} community", target=community.name, severity=AuditLog.WARNING)
            messages.success(request, f"{community.name} has been {state}.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'communities'}))

        elif action == "toggle_community_post_policy":
            community = get_object_or_404(Community, pk=request.POST.get("community_id"))
            community.allow_member_posts = not community.allow_member_posts
            community.save(update_fields=["allow_member_posts"])
            state = "enabled" if community.allow_member_posts else "disabled"
            _record_audit(request.user, f"Toggled community posting", target=community.name)
            messages.success(request, f"Member posting has been {state} for {community.name}.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'communities'}))

        elif action == "transfer_community_owner":
            community = get_object_or_404(Community, pk=request.POST.get("community_id"))
            new_owner = get_object_or_404(get_user_model(), pk=request.POST.get("new_owner_id"))
            previous_owner = community.owner
            if new_owner == previous_owner:
                messages.error(request, "Please choose a different owner.")
            else:
                community.owner = new_owner
                community.save(update_fields=["owner"])
                CommunityMembership.objects.get_or_create(
                    community=community,
                    user=new_owner,
                    defaults={"role": CommunityMembership.OWNER},
                )
                _record_audit(request.user, "Transferred community ownership", target=community.name, details=f"from {previous_owner.username} to {new_owner.username}")
                messages.success(request, f"{community.name} ownership transferred to {new_owner.username}.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'communities'}))

        elif action == "set_member_role":
            membership = get_object_or_404(CommunityMembership, pk=request.POST.get("membership_id"))
            if membership.role == CommunityMembership.OWNER:
                messages.error(request, "You cannot change the owner role here.")
            else:
                role = request.POST.get("role")
                if role in [CommunityMembership.MEMBER, CommunityMembership.MODERATOR]:
                    membership.role = role
                    membership.save(update_fields=["role"])
                    _record_audit(request.user, "Updated community member role", target=f"{membership.user.username} in {membership.community.name}")
                    messages.success(request, f"{membership.user.username} role updated to {role}." )
                else:
                    messages.error(request, "Invalid role selected.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'communities'}))

        elif action == "remove_member":
            membership = get_object_or_404(CommunityMembership, pk=request.POST.get("membership_id"))
            if membership.role == CommunityMembership.OWNER:
                messages.error(request, "You cannot remove the community owner from membership here.")
            else:
                target = f"{membership.user.username} from {membership.community.name}"
                membership.delete()
                _record_audit(request.user, "Removed community member", target=target, severity=AuditLog.WARNING)
                messages.success(request, f"{target} has been removed.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'communities'}))

        elif action == "create_post":
            post_form = AdminCommunityPostForm(request.POST)
            if post_form.is_valid():
                post = post_form.save()
                if not post.ai_summary:
                    post.ai_summary = _generate_ai_summary(post.body)
                    post.save(update_fields=["ai_summary"])
                _record_audit(request.user, "Created post", target=post.title)
                messages.success(request, f"{post.title} has been published.")
                return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'posts'}))
            messages.error(request, "Please correct the post form and try again.")

        elif action == "delete_post":
            post = get_object_or_404(CommunityPost, pk=request.POST.get("post_id"))
            post_title = post.title
            post.delete()
            _record_audit(request.user, "Deleted post", target=post_title, severity=AuditLog.WARNING)
            messages.success(request, f"{post_title} has been deleted.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'posts'}))

        elif action == "review_report":
            report = get_object_or_404(ContentReport, pk=request.POST.get("report_id"))
            status = request.POST.get("status", ContentReport.IN_REVIEW)
            if status not in dict(ContentReport.STATUS_CHOICES):
                status = ContentReport.IN_REVIEW
            report.status = status
            report.resolution_notes = request.POST.get("resolution_notes", "").strip()
            report.reviewed_by = request.user
            report.save(update_fields=["status", "resolution_notes", "reviewed_by", "updated_at"])
            if report.post and status in {ContentReport.RESOLVED, ContentReport.ESCALATED}:
                report.post.safety_status = CommunityPost.REMOVED if status == ContentReport.ESCALATED else CommunityPost.FLAGGED
                report.post.removed_at = timezone.now() if status == ContentReport.ESCALATED else None
                report.post.moderation_notes = report.resolution_notes
                report.post.save(update_fields=["safety_status", "removed_at", "moderation_notes"])
            _record_audit(request.user, "Reviewed content report", target=str(report.pk), severity=AuditLog.WARNING)
            messages.success(request, "Report review saved.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'moderation'}))

        elif action == "create_plan":
            if not _is_platform_owner(request.user):
                messages.error(request, "Only the platform owner can create subscription plans.")
                return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'overview'}))
            plan_form = SubscriptionPlanForm(request.POST)
            if plan_form.is_valid():
                plan = plan_form.save()
                _record_audit(request.user, "Created subscription plan", target=plan.name)
                messages.success(request, f"{plan.name} plan saved.")
                return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'subscriptions'}))
            messages.error(request, "Please correct the subscription plan form.")

        elif action == "toggle_plan":
            if not _is_platform_owner(request.user):
                messages.error(request, "Only the platform owner can change subscription plans.")
                return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'overview'}))
            plan = get_object_or_404(SubscriptionPlan, pk=request.POST.get("plan_id"))
            plan.is_active = not plan.is_active
            plan.save(update_fields=["is_active"])
            _record_audit(request.user, "Toggled subscription plan", target=plan.name)
            messages.success(request, f"{plan.name} is now {'active' if plan.is_active else 'inactive'}.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'subscriptions'}))

        elif action == "mark_backup":
            _record_audit(
                request.user,
                "Backup checkpoint recorded",
                target="Local database",
                details="Manual backup checkpoint from super admin dashboard.",
            )
            messages.success(request, "Backup checkpoint recorded in audit logs.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'system'}))

        elif action == "manage_thread":
            thread_id = request.POST.get("thread_id")
            new_status = request.POST.get("status")
            if thread_id and new_status in [DirectMessageThread.ACTIVE, DirectMessageThread.ARCHIVED, DirectMessageThread.BLOCKED]:
                thread = get_object_or_404(DirectMessageThread, id=thread_id)
                thread.status = new_status
                thread.save(update_fields=["status"])
                _record_audit(
                    request.user,
                    "Managed message thread",
                    target=f"{thread.participant1.username} <-> {thread.participant2.username}",
                    details=f"Changed status to {new_status}",
                )
                messages.success(request, f"Thread status updated to {new_status}.")
            return HttpResponseRedirect(reverse('hub:dashboard_section', kwargs={'section': 'messages'}))

        else:
            messages.error(request, "Unknown dashboard action.")

    User = get_user_model()
    users = User.objects.select_related("profile").annotate(
        community_count=Count("community_memberships", distinct=True),
        post_count=Count("community_posts", distinct=True),
    ).order_by("-is_superuser", "-is_staff", "first_name", "email", "username")[:12]
    communities = _community_cards(
        Community.objects.select_related("owner")
        .prefetch_related("memberships__user")
        .order_by("name")
    )[:12]
    active_users = User.objects.filter(is_active=True).order_by("first_name", "email", "username")[:100]
    posts = CommunityPost.objects.select_related("community", "author_user")[:10]
    contact_messages = ContactMessage.objects.all()[:6]
    message_threads = DirectMessageThread.objects.prefetch_related("messages").order_by("-last_message_at")[:20]
    reports = ContentReport.objects.select_related("reporter", "reported_user", "community", "post").all()[:10]
    can_manage_platform_finance = _is_platform_owner(request.user)
    plans = SubscriptionPlan.objects.all() if can_manage_platform_finance else SubscriptionPlan.objects.none()
    transactions = (
        PaymentTransaction.objects.select_related("user", "plan").all()[:10]
        if can_manage_platform_finance else PaymentTransaction.objects.none()
    )
    audit_logs = AuditLog.objects.select_related("actor").all()[:12]
    total_revenue = (
        PaymentTransaction.objects.filter(status=PaymentTransaction.SUCCESS).aggregate(total=Sum("amount"))["total"]
        or 0
    ) if can_manage_platform_finance else 0
    pending_count = ContentReport.objects.filter(status=ContentReport.PENDING).count()
    under_review_count = ContentReport.objects.filter(status=ContentReport.IN_REVIEW).count()
    admin_activity = [
        {
            "label": "Inactive Users",
            "value": f"{User.objects.filter(is_active=False).count():,}",
            "helper": "accounts currently disabled",
            "icon": "fa-user-slash",
        },
        {
            "label": "Private Spaces",
            "value": f"{Community.objects.filter(visibility=Community.PRIVATE).count():,}",
            "helper": "restricted communities",
            "icon": "fa-lock",
        },
        {
            "label": "Messages",
            "value": f"{ContactMessage.objects.count():,}",
            "helper": "contact requests",
            "icon": "fa-envelope",
        },
        {
            "label": "Admin Users",
            "value": f"{User.objects.filter(profile__platform_role=UserProfile.PLATFORM_ADMIN).count():,}",
            "helper": "platform admin accounts",
            "icon": "fa-user-shield",
        },
        {
            "label": "Open Reports",
            "value": f"{ContentReport.objects.exclude(status__in=[ContentReport.RESOLVED, ContentReport.DISMISSED]).count():,}",
            "helper": "moderation queue",
            "icon": "fa-shield-halved",
        },
        {
            "label": "Audit Logs",
            "value": f"{AuditLog.objects.count():,}",
            "helper": "accountability records",
            "icon": "fa-clipboard-check",
        },
    ]

    section_label = next((label for key, label, _ in SUPER_ADMIN_SECTION_ORDER if key == section), "Overview")

    return render(
        request,
        "admin-dashboard/super_admin.html",
        {
            "display_name": _display_name(request.user),
            "today": timezone.localdate(),
            "stats": _dashboard_stats(),
            "insights": _superadmin_insights(),
            "chart_data": _superadmin_chart_data(),
            "admin_activity": admin_activity,
            "user_form": user_form,
            "community_form": community_form,
            "post_form": post_form,
            "plan_form": plan_form,
            "users": users,
            "active_users": active_users,
            "communities": communities,
            "posts": posts,
            "contact_messages": contact_messages,
            "message_threads": message_threads,
            "reports": reports,
            "plans": plans,
            "transactions": transactions,
            "audit_logs": audit_logs,
            "active_plans": SubscriptionPlan.objects.filter(is_active=True).count() if can_manage_platform_finance else 0,
            "total_revenue": total_revenue,
            "can_manage_platform_finance": can_manage_platform_finance,
            "can_view_platform_finance": can_manage_platform_finance,
            "pending_count": pending_count,
            "under_review_count": under_review_count,
            "report_status_choices": ContentReport.STATUS_CHOICES,
            "session_info": _session_context(request),
            "active_section": section,
            "section_label": section_label,
            "super_admin_sections": [
                {"key": key, "label": label, "icon": icon, "url": reverse("hub:dashboard_section", kwargs={"section": key})}
                for key, label, icon in SUPER_ADMIN_SECTION_ORDER
                if can_manage_platform_finance or key != "subscriptions"
            ],
        },
    )


@require_POST
@csrf_exempt
def signout(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("hub:signin")


@login_required
def user_profile(request, username):
    User = get_user_model()
    profile_user = get_object_or_404(User, username=username)
    target_profile = getattr(profile_user, "profile", None)
    communities = Community.objects.filter(memberships__user=profile_user, is_archived=False).order_by("name")[:12]
    managed = Community.objects.filter(owner=profile_user, is_archived=False).order_by("name")[:12]
    posts = CommunityPost.objects.filter(author_user=profile_user).select_related("community").order_by("-created_at")[:10]
    post_count = CommunityPost.objects.filter(author_user=profile_user).count()
    member_count = CommunityMembership.objects.filter(user=profile_user).count()
    subscription = _active_subscription_for(profile_user)
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    is_following = UserFollow.objects.filter(follower=request.user, followed=profile_user).exists()
    return render(request, "user-dashboard/sections/profile.html", {
        "profile_user": profile_user,
        "target_profile": target_profile,
        "communities": communities,
        "managed": managed,
        "posts": posts,
        "post_count": post_count,
        "member_count": member_count,
        "subscription": subscription,
        "is_following": is_following,
        "display_name": _display_name(request.user),
        "active_page": "profile",
        "profile": user_profile,
        "user": request.user,
        "today": timezone.localdate(),
        "notification_count": _unread_notification_count(request.user),
        "notifications": _notifications_for_user(request.user),
        "active_subscription": _active_subscription_for(request.user),
        "user_dashboard_sections": _user_dashboard_sections(),
    })


@login_required
def get_message_thread(request, thread_id):
    """API endpoint to get messages from a thread."""
    user = request.user
    try:
        thread = DirectMessageThread.objects.filter(
            Q(id=thread_id) & (Q(participant1=user) | Q(participant2=user))
        ).get()
    except DirectMessageThread.DoesNotExist:
        return HttpResponse(json.dumps({"error": "Thread not found"}), status=404, content_type="application/json")

    # Mark messages as read
    DirectMessage.objects.filter(thread=thread, is_read=False).exclude(sender=user).update(is_read=True)

    # Get all messages in the thread
    thread_messages = DirectMessage.objects.filter(thread=thread).select_related("sender").order_by("created_at")

    # Determine the other participant
    other_user = thread.participant2 if thread.participant1 == user else thread.participant1

    # Format response
    messages_data = []
    for msg in thread_messages:
        sender_profile = getattr(msg.sender, "profile", None)
        attachment_url = msg.attachment.url if msg.attachment and msg.attachment.name else ""
        attachment_name = msg.attachment.name.rsplit("/", 1)[-1] if msg.attachment and msg.attachment.name else ""
        attachment_extension = attachment_name.rsplit(".", 1)[-1].lower() if "." in attachment_name else ""
        attachment_kind = "image" if attachment_extension in {"jpg", "jpeg", "png", "gif", "webp"} else "video" if attachment_extension in {"mp4", "webm", "mov", "m4v"} else "file" if attachment_name else ""
        messages_data.append({
            "id": msg.id,
            "body": msg.body,
            "sender": msg.sender.get_full_name() or msg.sender.username,
            "sender_avatar": sender_profile.profile_image_url if sender_profile else "",
            "is_sent": msg.sender == user,
            "time": msg.created_at.strftime("%I:%M %p"),
            "date": msg.created_at.strftime("%b %d, %Y"),
            "is_read": msg.is_read,
            "attachment_url": attachment_url,
            "attachment_name": attachment_name,
            "attachment_kind": attachment_kind,
        })

    other_profile = getattr(other_user, "profile", None)
    other_name = other_user.get_full_name() or other_user.username
    other_avatar = other_profile.profile_image_url if other_profile else ""
    return HttpResponse(
        json.dumps({
            "thread_id": thread.id,
            "other_user_name": other_name,
            "other_user_username": other_user.username,
            "other_user_avatar": other_avatar,
            "other_user_profile_url": reverse("hub:user_profile", kwargs={"username": other_user.username}),
            "other_user_bio": other_profile.bio if other_profile else "",
            "other_user_status": other_profile.custom_status if other_profile and other_profile.custom_status else "Active on Union",
            "other_user_presence": other_profile.presence_status if other_profile else "offline",
            "other_user_joined": other_user.date_joined.strftime("%B %Y"),
            "messages": messages_data,
        }),
        content_type="application/json"
    )


# ---------------------------------------------------------------------------
# Community Rooms & Links CRUD
# ---------------------------------------------------------------------------


def _get_community_and_manager(request, slug):
    community = get_object_or_404(Community, slug=slug)
    is_manager = community.owner_id == request.user.id or CommunityMembership.objects.filter(
        community=community,
        user=request.user,
        role__in=[CommunityMembership.MODERATOR],
        status=CommunityMembership.ACTIVE,
    ).exists()
    return community, is_manager


@login_required
def add_room(request, slug):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    form = CommunityRoomForm(request.POST)
    if form.is_valid():
        room = form.save(commit=False)
        room.community = community
        room.save()
        return JsonResponse({
            "ok": True,
            "room": {
                "id": room.id,
                "name": room.name,
                "icon_class": room.icon_class,
                "icon_image_url": room.icon_image_url,
                "color": room.color,
                "display_order": room.display_order,
            },
        })
    return JsonResponse({"ok": False, "errors": form.errors}, status=400)


@login_required
def edit_room(request, slug, pk):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    room = get_object_or_404(CommunityRoom, pk=pk, community=community)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    form = CommunityRoomForm(request.POST, instance=room)
    if form.is_valid():
        room = form.save()
        return JsonResponse({
            "ok": True,
            "room": {
                "id": room.id,
                "name": room.name,
                "icon_class": room.icon_class,
                "icon_image_url": room.icon_image_url,
                "color": room.color,
                "display_order": room.display_order,
            },
        })
    return JsonResponse({"ok": False, "errors": form.errors}, status=400)


@login_required
def delete_room(request, slug, pk):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    room = get_object_or_404(CommunityRoom, pk=pk, community=community)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    room.delete()
    return JsonResponse({"ok": True})


@login_required
def reorder_rooms(request, slug):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    try:
        data = json.loads(request.body)
        order = data.get("order", [])
        for idx, room_id in enumerate(order):
            CommunityRoom.objects.filter(pk=room_id, community=community).update(display_order=idx)
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid data."}, status=400)


@login_required
def add_link(request, slug):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    form = CommunityLinkForm(request.POST)
    if form.is_valid():
        link = form.save(commit=False)
        link.community = community
        link.title = (link.title or "").strip()
        link.icon_class, link.icon_image_url = _infer_link_icon_metadata(link.url)
        link.display_order = community.links.count()
        link.save()
        return JsonResponse({
            "ok": True,
            "link": {
                "id": link.id,
                "title": link.title,
                "url": link.url,
                "icon_class": link.icon_class,
                "icon_image_url": link.icon_image_url,
                "open_in_new_tab": link.open_in_new_tab,
                "display_order": link.display_order,
            },
        })
    return JsonResponse({"ok": False, "errors": form.errors}, status=400)


@login_required
def edit_link(request, slug, pk):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    link = get_object_or_404(CommunityLink, pk=pk, community=community)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    form = CommunityLinkForm(request.POST, instance=link)
    if form.is_valid():
        link = form.save(commit=False)
        link.title = (link.title or "").strip()
        link.icon_class, link.icon_image_url = _infer_link_icon_metadata(link.url)
        link.save()
        return JsonResponse({
            "ok": True,
            "link": {
                "id": link.id,
                "title": link.title,
                "url": link.url,
                "icon_class": link.icon_class,
                "icon_image_url": link.icon_image_url,
                "open_in_new_tab": link.open_in_new_tab,
                "display_order": link.display_order,
            },
        })
    return JsonResponse({"ok": False, "errors": form.errors}, status=400)


@login_required
def delete_link(request, slug, pk):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    link = get_object_or_404(CommunityLink, pk=pk, community=community)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    link.delete()
    return JsonResponse({"ok": True})


@login_required
def reorder_links(request, slug):
    community, is_manager = _get_community_and_manager(request, slug)
    if not is_manager:
        return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    try:
        data = json.loads(request.body)
        order = data.get("order", [])
        for idx, link_id in enumerate(order):
            CommunityLink.objects.filter(pk=link_id, community=community).update(display_order=idx)
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid data."}, status=400)


@login_required
def notification_action(request):
    user = request.user
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only."}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    action = data.get("action")
    pk = data.get("pk")

    if action == "mark_all_read":
        Notification.objects.filter(user=user, is_read=False).update(is_read=True)
        return JsonResponse({"ok": True})

    if action in ("read", "unread"):
        if not pk:
            return JsonResponse({"ok": False, "error": "Missing pk."}, status=400)
        Notification.objects.filter(pk=pk, user=user).update(is_read=(action == "read"))
        return JsonResponse({"ok": True})

    if action == "archive":
        if not pk:
            return JsonResponse({"ok": False, "error": "Missing pk."}, status=400)
        Notification.objects.filter(pk=pk, user=user).update(is_archived=True)
        return JsonResponse({"ok": True})

    if action == "delete":
        if not pk:
            return JsonResponse({"ok": False, "error": "Missing pk."}, status=400)
        Notification.objects.filter(pk=pk, user=user).delete()
        return JsonResponse({"ok": True})

    return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)


@login_required
def friend_request(request):
    user = request.user
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only."}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    action = data.get("action")
    target_id = data.get("target_id")

    if not target_id:
        return JsonResponse({"ok": False, "error": "Missing target_id."}, status=400)

    User = get_user_model()
    target = get_object_or_404(User, pk=target_id)
    if target == user:
        return JsonResponse({"ok": False, "error": "Cannot friend yourself."}, status=400)

    display_name = user.first_name or user.username

    if action == "send":
        follow, created = UserFollow.objects.get_or_create(follower=user, followed=target, defaults={"status": UserFollow.PENDING})
        if not created:
            return JsonResponse({"ok": False, "error": "Request already exists."}, status=400)
        _notify(
            target,
            "Friend request",
            f"{display_name} sent you a friend request.",
            actor=user,
            notification_type=Notification.FOLLOW,
        )
        return JsonResponse({"ok": True, "status": "pending"})

    if action == "accept":
        updated = UserFollow.objects.filter(follower=target, followed=user, status=UserFollow.PENDING).update(status=UserFollow.ACCEPTED)
        if not updated:
            return JsonResponse({"ok": False, "error": "No pending request."}, status=400)
        UserFollow.objects.get_or_create(follower=user, followed=target, defaults={"status": UserFollow.ACCEPTED})
        _notify(
            target,
            "Friend request accepted",
            f"{display_name} accepted your friend request.",
            actor=user,
            notification_type=Notification.FOLLOW,
        )
        return JsonResponse({"ok": True, "status": "accepted"})

    if action == "decline":
        updated = UserFollow.objects.filter(follower=target, followed=user, status=UserFollow.PENDING).update(status=UserFollow.REJECTED)
        if not updated:
            return JsonResponse({"ok": False, "error": "No pending request."}, status=400)
        return JsonResponse({"ok": True, "status": "rejected"})

    if action == "cancel":
        deleted, _ = UserFollow.objects.filter(follower=user, followed=target, status=UserFollow.PENDING).delete()
        if not deleted:
            return JsonResponse({"ok": False, "error": "No pending request to cancel."}, status=400)
        return JsonResponse({"ok": True, "status": "cancelled"})

    if action == "remove":
        UserFollow.objects.filter(Q(follower=user, followed=target) | Q(follower=target, followed=user)).delete()
        return JsonResponse({"ok": True, "status": "removed"})

    return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)


@login_required
def dm_chat(request, user_id):
    user = request.user
    other = get_object_or_404(get_user_model(), pk=user_id)
    if other == user:
        return JsonResponse({"ok": False, "error": "Cannot message yourself."}, status=400)

    p1, p2 = sorted([user.pk, other.pk])
    thread, _ = DirectMessageThread.objects.get_or_create(
        participant1_id=p1, participant2_id=p2,
        defaults={"status": DirectMessageThread.ACTIVE},
    )

    if request.method == "POST":
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
        body = (data.get("body") or "").strip()
        if not body:
            return JsonResponse({"ok": False, "error": "Empty message."}, status=400)
        msg = DirectMessage.objects.create(thread=thread, sender=user, body=body)
        thread.last_message_at = msg.created_at
        thread.save(update_fields=["last_message_at"])
        return JsonResponse({"ok": True, "id": msg.id, "body": msg.body, "time": msg.created_at.strftime("%I:%M %p")})

    DirectMessage.objects.filter(thread=thread, is_read=False).exclude(sender=user).update(is_read=True)
    msgs = DirectMessage.objects.filter(thread=thread).select_related("sender").order_by("created_at")
    messages_data = [
        {"id": m.id, "body": m.body, "sender": m.sender.username, "is_sent": m.sender == user, "time": m.created_at.strftime("%I:%M %p")}
        for m in msgs
    ]
    return JsonResponse({
        "ok": True,
        "thread_id": thread.id,
        "other_user": {"id": other.id, "username": other.username, "avatar": other.profile.profile_image_url or ""},
        "messages": messages_data,
    })
