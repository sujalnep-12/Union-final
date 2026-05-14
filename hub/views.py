import random
import logging
from datetime import timedelta

from django.contrib import messages

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db.models import Count, Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    AdminCommunityForm,
    AdminCommunityPostForm,
    AdminUserCreationForm,
    CommunityForm,
    CommunityPostForm,
    ContactMessageForm,
    ContentReportForm,
    PostCommentForm,
    SubscriptionPlanForm,
    UserProfileForm,
)
from .models import (
    AuditLog,
    Community,
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


REMEMBER_ME_SESSION_AGE = 60 * 60 * 24 * 14
logger = logging.getLogger(__name__)

NON_DELIVERY_EMAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
}


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


USER_DASHBOARD_SECTIONS = {
    "overview",
    "communities",
    "content",
    "messages",
    "notifications",
    "subscriptions",
    "reports",
    "payments",
    "settings",
}


def _dashboard_next_url():
    return reverse("hub:dashboard")


def _dashboard_section_url(section):
    if section == "overview":
        return reverse("hub:dashboard")
    return reverse("hub:dashboard_section", kwargs={"section": section})


def _remember_me_enabled(request):
    return request.POST.get("remember_me") == "on"


def _start_authenticated_session(request, user, *, remember_me=False, entrypoint="signin"):
    login(request, user)
    if remember_me:
        request.session.set_expiry(REMEMBER_ME_SESSION_AGE)
    else:
        request.session.set_expiry(0)
    request.session["auth_entrypoint"] = entrypoint
    request.session["remember_me"] = remember_me
    request.session["authenticated_at"] = timezone.now().isoformat()


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
    context = {"gender_choices": UserProfile.GENDER_CHOICES}
    context.update(extra)
    return context


def _generate_otp():
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def _email_backend_sends_real_email():
    return settings.EMAIL_BACKEND not in NON_DELIVERY_EMAIL_BACKENDS


def _store_debug_otp(request, otp_code, *, email_sent):
    if settings.DEBUG and (not email_sent or not _email_backend_sends_real_email()):
        request.session["debug_otp_code"] = otp_code
    else:
        request.session.pop("debug_otp_code", None)


def _otp_context(request, user, next_url):
    return {
        "email": user.email,
        "next": next_url,
        "debug_otp_code": request.session.get("debug_otp_code") if settings.DEBUG else "",
    }


def _send_verification_otp(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    otp_code = _generate_otp()
    profile.set_otp(otp_code)
    profile.save(update_fields=["otp_code", "otp_created_at"])
    try:
        sent_count = send_mail(
            "Your Union verification code",
            (
                f"Hi {_display_name(user)},\n\n"
                f"Your Union verification code is {otp_code}.\n\n"
                "Enter this code to finish creating your account. "
                "After verification, log in with your username and password."
            ),
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send verification OTP to %s", user.email)
        return False, otp_code

    return sent_count > 0, otp_code


def _add_otp_delivery_message(request, *, email_sent, created=False):
    if email_sent and _email_backend_sends_real_email():
        detail = "Account created. " if created else ""
        messages.success(request, f"{detail}Check your email for the OTP code.")
        return

    if email_sent:
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


def _community_cards(queryset):
    return queryset.annotate(
        member_count=Count("memberships", distinct=True),
        post_count=Count("posts", distinct=True),
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
    if request.user.is_authenticated:
        return redirect(_safe_next_url(request) or "hub:dashboard")

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

    return render(request, "signup.html", _signup_context(next=next_url))


def verify_otp(request):
    User = get_user_model()
    user_id = request.session.get("pending_verification_user_id")
    user = User.objects.filter(pk=user_id, is_active=False).first()
    if user is None:
        messages.error(request, "No pending account verification was found.")
        return redirect("hub:signup")

    next_url = request.session.get("pending_verification_next") or _dashboard_next_url()

    if request.method == "POST":
        action = request.POST.get("action", "verify")
        profile = get_object_or_404(UserProfile, user=user)

        if action == "resend":
            email_sent, otp_code = _send_verification_otp(user)
            _store_debug_otp(request, otp_code, email_sent=email_sent)
            _add_otp_delivery_message(request, email_sent=email_sent)
            return redirect("hub:verify_otp")

        otp_code = request.POST.get("otp_code", "").strip()
        if otp_code != profile.otp_code:
            messages.error(request, "Invalid OTP code. Please check your email and try again.")
            return render(request, "verify_otp.html", _otp_context(request, user, next_url))

        user.is_active = True
        user.save(update_fields=["is_active"])
        profile.email_verified = True
        profile.otp_code = ""
        profile.save(update_fields=["email_verified", "otp_code"])
        request.session.pop("pending_verification_user_id", None)
        request.session.pop("pending_verification_next", None)
        request.session.pop("debug_otp_code", None)
        messages.success(request, "Email verified. Please log in with your username and password.")
        return redirect(f"{reverse('hub:signin')}?next={next_url}")

    return render(request, "verify_otp.html", _otp_context(request, user, next_url))


def signin(request):
    if request.user.is_authenticated:
        return redirect("hub:dashboard")

    next_url = _safe_next_url(request)

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        form_data = {"username": username}

        if not username or not password:
            messages.error(request, "Please enter your username and password.")
            return render(request, "signin.html", {"form_data": form_data, "next": next_url})

        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, "Invalid username or password.")
            return render(request, "signin.html", {"form_data": form_data, "next": next_url})

        if not user.is_active:
            messages.error(request, "This account is not active. Please verify your OTP before logging in.")
            return render(request, "signin.html", {"form_data": form_data, "next": next_url})

        _start_authenticated_session(
            request,
            user,
            remember_me=_remember_me_enabled(request),
            entrypoint="signin",
        )
        return redirect(next_url or "hub:dashboard")

    return render(request, "signin.html", {"next": next_url})


def forgot_password(request):
    form_data = {}

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        form_data = {"email": email}

        if not email:
            messages.error(request, "Please enter your email address.")
            return render(request, "forgetpw.html", {"form_data": form_data})

        messages.success(
            request,
            "If an account exists for that email, password reset instructions will be sent.",
        )
        return render(request, "forgetpw.html", {"form_data": form_data})

    return render(request, "forgetpw.html")


@login_required
def dashboard(request, section="overview"):
    if request.user.is_superuser:
        return super_admin_dashboard(request)
    if section not in USER_DASHBOARD_SECTIONS:
        return redirect("hub:dashboard")
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


def _user_dashboard_stats():
    today = timezone.localdate()
    month_start = today.replace(day=1)
    monthly_revenue = (
        PaymentTransaction.objects.filter(
            status=PaymentTransaction.SUCCESS,
            created_at__date__gte=month_start,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    return [
        {
            "label": "Total Users",
            "value": _compact_number(get_user_model().objects.count()),
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


def _record_audit(actor, action, *, target="", details="", severity=AuditLog.INFO):
    AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target=target,
        details=details,
        severity=severity,
    )


def _notify(user, title, body, *, category=Notification.ACCOUNT):
    Notification.objects.create(user=user, title=title, body=body, category=category)


def _ensure_default_subscription_plans():
    defaults = [
        {
            "slug": SubscriptionPlan.BASIC,
            "name": "Basic",
            "price": "0.00",
            "community_limit": 1,
            "description": "Starter access for individual members.",
            "feature_summary": "Create 1 community, join public communities, post updates, report content.",
        },
        {
            "slug": SubscriptionPlan.STANDARD,
            "name": "Standard",
            "price": "499.00",
            "community_limit": 5,
            "description": "Growth plan for active creators.",
            "feature_summary": "Create up to 5 communities, pin posts, receive payment notifications.",
        },
        {
            "slug": SubscriptionPlan.PREMIUM,
            "name": "Premium",
            "price": "999.00",
            "community_limit": 20,
            "description": "Advanced plan for larger community programs.",
            "feature_summary": "Create up to 20 communities, advanced analytics placeholders, priority moderation review.",
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


def user_dashboard(request, section="overview"):
    user = request.user
    display_name = _display_name(user)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    _ensure_default_subscription_plans()
    community_form = CommunityForm()
    post_form = CommunityPostForm(user=user, initial={"category": "General"})
    profile_form = UserProfileForm(instance=profile, user=user)
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
            community_form = CommunityForm(request.POST)
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
            else:
                CommunityMembership.objects.get_or_create(community=community, user=user)
                _notify(user, "Community joined", f"You joined {community.name}.", category=Notification.COMMUNITY)
                _record_audit(user, "Joined community", target=community.name)
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
                    post.ai_summary = post.body[:180]
                post.save()
                _record_audit(user, "Created post", target=post.title)
                messages.success(request, "Your post has been published.")
                return HttpResponseRedirect(_dashboard_section_url("content"))
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
                messages.success(request, "Like removed.")
            else:
                messages.success(request, "Post liked.")
            return HttpResponseRedirect(_dashboard_section_url("content"))

        elif action == "share_post":
            post = get_object_or_404(_visible_posts_for(user), pk=request.POST.get("post_id"))
            PostEngagement.objects.get_or_create(
                post=post,
                user=user,
                engagement_type=PostEngagement.SHARE,
            )
            messages.success(request, "Post shared inside your activity history.")
            return HttpResponseRedirect(_dashboard_section_url("content"))

        elif action == "pin_post":
            post = get_object_or_404(CommunityPost, pk=request.POST.get("post_id"), author_user=user)
            post.is_pinned = not post.is_pinned
            post.save(update_fields=["is_pinned"])
            messages.success(request, "Post pin status updated.")
            return HttpResponseRedirect(_dashboard_section_url("content"))

        elif action == "create_comment":
            post = get_object_or_404(_visible_posts_for(user), pk=request.POST.get("post_id"))
            comment_form = PostCommentForm(request.POST)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.post = post
                comment.author = user
                comment.save()
                messages.success(request, "Comment added.")
                return HttpResponseRedirect(_dashboard_section_url("content"))
            messages.error(request, "Please write a comment before submitting.")

        elif action == "report_post":
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
            transaction = PaymentTransaction.objects.create(
                user=user,
                plan=plan,
                subscription=subscription,
                gateway=gateway,
                status=PaymentTransaction.SUCCESS,
                amount=plan.price,
                reference_code=f"UNION-{timezone.now().strftime('%Y%m%d%H%M%S')}-{user.pk}",
                notes="Local sandbox transaction record.",
            )
            _notify(
                user,
                "Payment confirmed",
                f"Your {plan.name} subscription is active. Reference: {transaction.reference_code}",
                category=Notification.PAYMENT,
            )
            _record_audit(user, "Purchased subscription", target=plan.name)
            messages.success(request, f"{plan.name} subscription is active.")
            return HttpResponseRedirect(_dashboard_section_url("subscriptions"))

        elif action == "dismiss_notification":
            Notification.objects.filter(pk=request.POST.get("notification_id"), user=user).update(is_read=True)
            return HttpResponseRedirect(_dashboard_section_url("notifications"))

        else:
            messages.error(request, "Unknown dashboard action.")

    user_communities = _community_cards(
        Community.objects.filter(memberships__user=user, is_archived=False).order_by("name")
    )
    discover_communities = _community_cards(
        Community.objects.filter(visibility=Community.PUBLIC, is_archived=False)
        .exclude(memberships__user=user)
        .order_by("name")
    )
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
    notifications = Notification.objects.filter(user=user, is_read=False)[:6]
    transactions = PaymentTransaction.objects.select_related("plan").filter(user=user)[:6]
    plans = SubscriptionPlan.objects.filter(is_active=True)
    active_subscription = _active_subscription_for(user)

    return render(
        request,
        "dashboard/user.html",
        {
            "display_name": display_name,
            "active_page": section,
            "profile": profile,
            "today": timezone.localdate(),
            "stats": _user_dashboard_stats(),
            "insights": _community_insights(),
            "community_form": community_form,
            "post_form": post_form,
            "profile_form": profile_form,
            "report_form": report_form,
            "comment_form": comment_form,
            "user_communities": user_communities,
            "discover_communities": discover_communities,
            "recent_posts": recent_posts,
            "reports": reports,
            "notifications": notifications,
            "transactions": transactions,
            "plans": plans,
            "active_subscription": active_subscription,
            "community_limit": _community_limit_for(user),
            "session_info": _session_context(request),
        },
    )


@login_required
def super_admin_dashboard(request):
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
                UserProfile.objects.get_or_create(user=new_user)
                _record_audit(request.user, "Created user", target=new_user.username)
                messages.success(request, f"{_display_name(new_user)} has been created.")
                return HttpResponseRedirect(f"{reverse('hub:dashboard')}#users")
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
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#users")

        elif action == "update_user_role":
            managed_user = get_object_or_404(get_user_model(), pk=request.POST.get("user_id"))
            if managed_user.is_superuser:
                messages.error(request, "The primary super admin role is managed separately.")
            else:
                managed_user.is_staff = request.POST.get("is_staff") == "on"
                managed_user.is_active = request.POST.get("is_active") == "on"
                managed_user.save(update_fields=["is_staff", "is_active"])
                _record_audit(request.user, "Updated user privileges", target=managed_user.username)
                messages.success(request, f"{_display_name(managed_user)} privileges updated.")
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#users")

        elif action == "delete_user":
            managed_user = get_object_or_404(get_user_model(), pk=request.POST.get("user_id"))
            if managed_user == request.user or managed_user.is_superuser:
                messages.error(request, "This account cannot be removed from the dashboard.")
            else:
                target = managed_user.username
                managed_user.delete()
                _record_audit(request.user, "Deleted user", target=target, severity=AuditLog.CRITICAL)
                messages.success(request, f"{target} has been removed.")
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#users")

        elif action == "create_community":
            community_form = AdminCommunityForm(request.POST)
            if community_form.is_valid():
                community = community_form.save()
                CommunityMembership.objects.get_or_create(
                    community=community,
                    user=community.owner,
                    defaults={"role": CommunityMembership.OWNER},
                )
                _record_audit(request.user, "Created community", target=community.name)
                messages.success(request, f"{community.name} has been created.")
                return HttpResponseRedirect(f"{reverse('hub:dashboard')}#communities")
            messages.error(request, "Please correct the community form and try again.")

        elif action == "delete_community":
            community = get_object_or_404(Community, pk=request.POST.get("community_id"))
            community_name = community.name
            community.delete()
            _record_audit(request.user, "Deleted community", target=community_name, severity=AuditLog.WARNING)
            messages.success(request, f"{community_name} has been deleted.")
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#communities")

        elif action == "create_post":
            post_form = AdminCommunityPostForm(request.POST)
            if post_form.is_valid():
                post = post_form.save()
                if not post.ai_summary:
                    post.ai_summary = post.body[:180]
                    post.save(update_fields=["ai_summary"])
                _record_audit(request.user, "Created post", target=post.title)
                messages.success(request, f"{post.title} has been published.")
                return HttpResponseRedirect(f"{reverse('hub:dashboard')}#posts")
            messages.error(request, "Please correct the post form and try again.")

        elif action == "delete_post":
            post = get_object_or_404(CommunityPost, pk=request.POST.get("post_id"))
            post_title = post.title
            post.delete()
            _record_audit(request.user, "Deleted post", target=post_title, severity=AuditLog.WARNING)
            messages.success(request, f"{post_title} has been deleted.")
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#posts")

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
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#moderation")

        elif action == "create_plan":
            plan_form = SubscriptionPlanForm(request.POST)
            if plan_form.is_valid():
                plan = plan_form.save()
                _record_audit(request.user, "Created subscription plan", target=plan.name)
                messages.success(request, f"{plan.name} plan saved.")
                return HttpResponseRedirect(f"{reverse('hub:dashboard')}#subscriptions")
            messages.error(request, "Please correct the subscription plan form.")

        elif action == "toggle_plan":
            plan = get_object_or_404(SubscriptionPlan, pk=request.POST.get("plan_id"))
            plan.is_active = not plan.is_active
            plan.save(update_fields=["is_active"])
            _record_audit(request.user, "Toggled subscription plan", target=plan.name)
            messages.success(request, f"{plan.name} is now {'active' if plan.is_active else 'inactive'}.")
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#subscriptions")

        elif action == "mark_backup":
            _record_audit(
                request.user,
                "Backup checkpoint recorded",
                target="Local database",
                details="Manual backup checkpoint from super admin dashboard.",
            )
            messages.success(request, "Backup checkpoint recorded in audit logs.")
            return HttpResponseRedirect(f"{reverse('hub:dashboard')}#system")

        else:
            messages.error(request, "Unknown dashboard action.")

    User = get_user_model()
    users = User.objects.annotate(
        community_count=Count("community_memberships", distinct=True),
        post_count=Count("community_posts", distinct=True),
    ).order_by("-is_superuser", "-is_staff", "first_name", "email", "username")[:12]
    communities = _community_cards(Community.objects.select_related("owner").order_by("name"))[:12]
    posts = CommunityPost.objects.select_related("community", "author_user")[:10]
    contact_messages = ContactMessage.objects.all()[:6]
    reports = ContentReport.objects.select_related("reporter", "reported_user", "community", "post").all()[:10]
    plans = SubscriptionPlan.objects.all()
    transactions = PaymentTransaction.objects.select_related("user", "plan").all()[:10]
    audit_logs = AuditLog.objects.select_related("actor").all()[:12]
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
            "value": f"{User.objects.filter(is_superuser=True).count():,}",
            "helper": "super admin accounts",
            "icon": "fa-user-shield",
        },
        {
            "label": "Open Reports",
            "value": f"{ContentReport.objects.exclude(status__in=[ContentReport.RESOLVED, ContentReport.DISMISSED]).count():,}",
            "helper": "moderation queue",
            "icon": "fa-shield-halved",
        },
        {
            "label": "Revenue",
            "value": f"Rs. {sum(transaction.amount for transaction in PaymentTransaction.objects.filter(status=PaymentTransaction.SUCCESS)):,}",
            "helper": "successful payments",
            "icon": "fa-money-bill-transfer",
        },
        {
            "label": "Plans",
            "value": f"{SubscriptionPlan.objects.filter(is_active=True).count():,}",
            "helper": "active subscriptions",
            "icon": "fa-layer-group",
        },
        {
            "label": "Audit Logs",
            "value": f"{AuditLog.objects.count():,}",
            "helper": "accountability records",
            "icon": "fa-clipboard-check",
        },
    ]

    return render(
        request,
        "dashboard/super_admin.html",
        {
            "display_name": _display_name(request.user),
            "today": timezone.localdate(),
            "stats": _dashboard_stats(),
            "admin_activity": admin_activity,
            "user_form": user_form,
            "community_form": community_form,
            "post_form": post_form,
            "plan_form": plan_form,
            "users": users,
            "communities": communities,
            "posts": posts,
            "contact_messages": contact_messages,
            "reports": reports,
            "plans": plans,
            "transactions": transactions,
            "audit_logs": audit_logs,
            "report_status_choices": ContentReport.STATUS_CHOICES,
            "session_info": _session_context(request),
        },
    )


@require_POST
def signout(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("hub:signin")
