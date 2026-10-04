import json
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    Announcement,
    Community,
    CommunityLink,
    CommunityMembership,
    CommunityPost,
    CommentEngagement,
    CommunityRoom,
    ContactMessage,
    DirectMessageThread,
    Notification,
    PaymentTransaction,
    PostDonation,
    PostComment,
    UserProfile,
)


@override_settings(
    ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class HubViewTests(TestCase):
    def create_member(self):
        return get_user_model().objects.create_user(
            username="member@example.com",
            email="member@example.com",
            password="StrongPass123!",
            first_name="Asha",
        )

    def test_signin_ignores_external_next_url(self):
        self.create_member()

        response = self.client.post(
            f"{reverse('hub:signin')}?next=https://evil.example/phish",
            {"username": "member@example.com", "password": "StrongPass123!"},
        )

        self.assertRedirects(response, reverse("hub:dashboard"), fetch_redirect_response=False)

    def test_signin_allows_local_next_url(self):
        self.create_member()

        response = self.client.post(
            f"{reverse('hub:signin')}?next=/admin/",
            {"username": "member@example.com", "password": "StrongPass123!"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")

    def test_switch_account_opens_signin_without_dropping_current_session(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(f"{reverse('hub:signin')}?switch_account=1&next=/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "signin.html")
        self.assertEqual(str(user.pk), self.client.session.get("_auth_user_id"))

    def test_get_started_sends_guest_to_signup_for_dashboard(self):
        response = self.client.get(reverse("hub:get_started"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('hub:signup')}?next={reverse('hub:dashboard')}")

    def test_get_started_sends_authenticated_user_to_dashboard(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:get_started"))

        self.assertRedirects(response, reverse("hub:dashboard"), fetch_redirect_response=False)

    def test_landing_shows_guest_auth_links(self):
        response = self.client.get(reverse("hub:home"))

        self.assertContains(response, reverse("hub:signin"))
        self.assertContains(response, reverse("hub:signup"))
        self.assertContains(response, "Login")
        self.assertContains(response, "Sign up")

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("hub:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(f"{reverse('hub:signin')}?next="))

    def test_dashboard_account_email_only_appears_in_account_popover(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard"))

        self.assertContains(response, 'id="sidebar-account-row"')
        self.assertContains(response, 'id="sidebar-account-popup" class="hidden fixed')
        self.assertContains(response, "Add account")
        self.assertContains(
            response,
            f'<p class="truncate text-sm font-semibold text-gray-900">{user.email}</p>',
        )

    def test_signup_creates_inactive_user_and_sends_otp(self):
        response = self.client.post(
            f"{reverse('hub:signup')}?next={reverse('hub:dashboard')}",
            {
                "full_name": "New Member",
                "username": "newmember",
                "email": "next-member@example.com",
                "date_of_birth": "2000-01-15",
                "gender": UserProfile.OTHER,
                "phone_number": "9800000000",
                "password": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("hub:verify_otp"), fetch_redirect_response=False)
        user = get_user_model().objects.get(username="newmember")
        self.assertFalse(user.is_active)
        self.assertEqual(user.email, "next-member@example.com")
        self.assertEqual(user.profile.phone_number, "9800000000")
        self.assertEqual(user.profile.gender, UserProfile.OTHER)
        self.assertEqual(len(user.profile.otp_code), 6)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(user.profile.otp_code, mail.outbox[0].body)

    def test_add_account_opens_signup_without_dropping_current_session(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(f"{reverse('hub:signup')}?add_account=1&next=/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "signup.html")
        self.assertEqual(str(user.pk), self.client.session.get("_auth_user_id"))

    def test_account_continue_routes_existing_email_to_switch_signin(self):
        current = self.create_member()
        other = get_user_model().objects.create_user(
            username="other@example.com",
            email="other@example.com",
            password="StrongPass123!",
            first_name="Other",
        )
        self.client.force_login(current)

        response = self.client.get(
            reverse("hub:account_continue"),
            {"email": other.email, "next": reverse("hub:dashboard")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"{reverse('hub:signin')}?", response["Location"])
        self.assertIn("switch_account=1", response["Location"])
        self.assertIn("other%40example.com", response["Location"])

    def test_switch_account_keeps_both_accounts_available(self):
        current = self.create_member()
        other = get_user_model().objects.create_user(
            username="other@example.com",
            email="other@example.com",
            password="StrongPass123!",
            first_name="Other",
        )
        self.client.force_login(current)
        session = self.client.session
        session["account_switch_user_ids"] = [current.pk, other.pk]
        session.save()

        response = self.client.post(
            reverse("hub:switch_account", kwargs={"user_id": other.pk}),
            {"next": reverse("hub:dashboard")},
        )

        self.assertRedirects(response, reverse("hub:dashboard"), fetch_redirect_response=False)
        self.assertEqual(str(other.pk), self.client.session.get("_auth_user_id"))
        self.assertEqual(self.client.session.get("account_switch_user_ids"), [current.pk, other.pk])

        response = self.client.get(reverse("hub:dashboard"))
        self.assertContains(response, "Other")
        self.assertContains(response, "Switch")

    def test_otp_verification_activates_account_then_requires_login(self):
        self.client.post(
            f"{reverse('hub:signup')}?next={reverse('hub:dashboard')}",
            {
                "full_name": "Remembered Member",
                "username": "remembered",
                "email": "remembered-member@example.com",
                "date_of_birth": "1999-03-20",
                "gender": UserProfile.FEMALE,
                "phone_number": "9811111111",
                "password": "StrongPass123!",
            },
        )
        user = get_user_model().objects.get(username="remembered")
        otp_code = user.profile.otp_code

        response = self.client.post(reverse("hub:verify_otp"), {"otp_code": otp_code})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{reverse('hub:signin')}?next={reverse('hub:dashboard')}")
        user.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.profile.email_verified)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_otp_verification_page_links_to_account_pages(self):
        user = self.create_member()
        user.is_active = False
        user.save(update_fields=["is_active"])
        session = self.client.session
        session["pending_verification_user_id"] = user.pk
        session.save()

        response = self.client.get(reverse("hub:verify_otp"))

        self.assertContains(response, reverse("hub:home"))
        self.assertContains(response, reverse("hub:signin"))
        self.assertContains(response, reverse("hub:signup"))

    def test_signup_ignores_external_next_url(self):
        response = self.client.post(
            f"{reverse('hub:signup')}?next=https://evil.example/phish",
            {
                "full_name": "Safe Member",
                "username": "safemember",
                "email": "safe-member@example.com",
                "date_of_birth": "2001-05-10",
                "gender": UserProfile.PREFER_NOT_TO_SAY,
                "phone_number": "9822222222",
                "password": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("hub:verify_otp"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["pending_verification_next"], reverse("hub:dashboard"))

    def test_signin_without_remember_me_uses_browser_session(self):
        self.create_member()

        response = self.client.post(
            reverse("hub:signin"),
            {"username": "member@example.com", "password": "StrongPass123!"},
        )

        self.assertRedirects(response, reverse("hub:dashboard"), fetch_redirect_response=False)
        session = self.client.session
        self.assertTrue(session.get_expire_at_browser_close())
        self.assertFalse(session["remember_me"])
        self.assertEqual(session["auth_entrypoint"], "signin")

    def test_signin_links_to_forgot_password(self):
        response = self.client.get(reverse("hub:signin"))

        self.assertContains(response, reverse("hub:forgot_password"))
        self.assertContains(response, "Forgot password?")

    def test_forgot_password_page_uses_union_logo_and_login_link(self):
        response = self.client.get(reverse("hub:forgot_password"))

        self.assertContains(response, "Union logo")
        self.assertContains(response, "images/union-logo.png")
        self.assertContains(response, reverse("hub:signin"))

    def test_forgot_password_post_shows_generic_success(self):
        response = self.client.post(
            reverse("hub:forgot_password"),
            {"email": "member@example.com"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "password reset instructions")

    def test_logout_requires_post(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_post_signs_user_out(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.post(reverse("hub:logout"))

        self.assertRedirects(response, reverse("hub:signin"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dashboard_search_route_returns_search_results(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Neighborhood Action",
            description="Local volunteer coordination",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            title="Community launch notes",
            author="Asha",
            category="General",
            body="Welcome to the first update for everyone in the hub.",
            author_user=user,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_search"), {"q": "welcome"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search Results")
        self.assertContains(response, post.body)
        self.assertTemplateUsed(response, "user-dashboard/sections/search_results.html")

    def test_add_link_saves_only_title_and_url_and_auto_fills_logo_metadata(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Neighborhood Action",
            description="Local volunteer coordination",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("hub:add_link", kwargs={"slug": community.slug}),
            {
                "title": "Official Website",
                "url": "https://example.com/hello",
            },
        )

        self.assertEqual(response.status_code, 200)

    def test_add_link_allows_missing_title_and_displays_url_instead(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Neighborhood Action",
            description="Local volunteer coordination",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("hub:add_link", kwargs={"slug": community.slug}),
            {
                "url": "https://example.com/hello",
            },
        )

        self.assertEqual(response.status_code, 200)
        link = CommunityLink.objects.get(community=community)
        self.assertEqual(link.title, "")
        self.assertEqual(link.url, "https://example.com/hello")
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["link"]["title"], "")
        self.assertEqual(data["link"]["url"], "https://example.com/hello")
        self.assertTrue(data["link"]["icon_image_url"])
        self.assertEqual(data["link"]["icon_class"], "fa-solid fa-globe")
        link = CommunityLink.objects.get(pk=data["link"]["id"])
        self.assertEqual(link.title, "")
        self.assertEqual(link.url, "https://example.com/hello")
        self.assertEqual(link.icon_class, "fa-solid fa-globe")
        self.assertTrue(link.icon_image_url)

    def test_room_crud_and_reorder_endpoints_work_for_managers(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Neighborhood Action",
            description="Local volunteer coordination",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        add_response = self.client.post(
            reverse("hub:add_room", kwargs={"slug": community.slug}),
            {
                "name": "Announcements",
                "room_type": CommunityRoom.ROOM_TYPE_ANNOUNCEMENTS,
                "layout": CommunityRoom.LAYOUT_BLANK,
                "description": "Pinned updates",
                "icon_class": "fa-solid fa-bullhorn",
                "icon_image_url": "",
                "icon_type": CommunityRoom.ICON_TYPE_SYSTEM,
                "color": "purple",
                "display_order": 0,
            },
        )

        self.assertEqual(add_response.status_code, 200)
        data = add_response.json()
        self.assertTrue(data["ok"])
        room = CommunityRoom.objects.get(pk=data["room"]["id"])
        self.assertEqual(room.name, "Announcements")
        self.assertEqual(room.room_type, CommunityRoom.ROOM_TYPE_ANNOUNCEMENTS)

        edit_response = self.client.post(
            reverse("hub:edit_room", kwargs={"slug": community.slug, "pk": room.pk}),
            {
                "name": "Updated Announcements",
                "room_type": CommunityRoom.ROOM_TYPE_ANNOUNCEMENTS,
                "layout": CommunityRoom.LAYOUT_BLANK,
                "description": "Updated description",
                "icon_class": "fa-solid fa-bullhorn",
                "icon_image_url": "",
                "icon_type": CommunityRoom.ICON_TYPE_SYSTEM,
                "color": "indigo",
                "display_order": 1,
            },
        )

        self.assertEqual(edit_response.status_code, 200)
        edit_data = edit_response.json()
        self.assertTrue(edit_data["ok"])
        room.refresh_from_db()
        self.assertEqual(room.name, "Updated Announcements")
        self.assertEqual(room.color, "indigo")
        self.assertEqual(room.display_order, 1)

        room_two = CommunityRoom.objects.create(
            community=community,
            name="Chat",
            room_type=CommunityRoom.ROOM_TYPE_CHAT,
            layout=CommunityRoom.LAYOUT_CHAT,
            description="Live chat",
            icon_class="fa-solid fa-comment-dots",
            icon_type=CommunityRoom.ICON_TYPE_SYSTEM,
            color="blue",
            display_order=2,
        )

        reorder_response = self.client.post(
            reverse("hub:reorder_rooms", kwargs={"slug": community.slug}),
            json.dumps({"order": [room_two.pk, room.pk]}),
            content_type="application/json",
        )

        self.assertEqual(reorder_response.status_code, 200)
        reorder_data = reorder_response.json()
        self.assertTrue(reorder_data["ok"])
        room.refresh_from_db()
        room_two.refresh_from_db()
        self.assertEqual(room.display_order, 1)
        self.assertEqual(room_two.display_order, 0)

        delete_response = self.client.post(
            reverse("hub:delete_room", kwargs={"slug": community.slug, "pk": room.pk}),
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(CommunityRoom.objects.filter(pk=room.pk).exists())

    def test_managers_can_create_an_announcement_in_the_room(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Neighborhood Action",
            description="Local volunteer coordination",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        room = CommunityRoom.objects.create(
            community=community,
            name="Announcements",
            room_type=CommunityRoom.ROOM_TYPE_ANNOUNCEMENTS,
            layout=CommunityRoom.LAYOUT_BLANK,
            description="Pinned updates",
            icon_class="fa-solid fa-bullhorn",
            icon_type=CommunityRoom.ICON_TYPE_SYSTEM,
            color="purple",
            display_order=0,
        )
        self.client.force_login(user)

        response = self.client.post(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?room={room.pk}",
            {
                "action": "create_announcement",
                "title": "Weekly update",
                "body": "The new workshop schedule is now published.",
                "is_pinned": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        announcement = Announcement.objects.get(community=community, title="Weekly update")
        self.assertEqual(announcement.body, "The new workshop schedule is now published.")
        self.assertTrue(announcement.is_pinned)
        self.assertEqual(announcement.created_by, user)

    def test_dashboard_creates_post_for_logged_in_user(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Neighborhood Action",
            description="Local volunteer coordination",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("hub:dashboard"),
            {
                "community": community.pk,
                "title": "Community launch notes",
                "category": "",
                "body": "Welcome to the first update for everyone in the hub.",
            },
        )

        self.assertRedirects(
            response,
            reverse("hub:dashboard"),
            fetch_redirect_response=False,
        )
        post = CommunityPost.objects.get()
        self.assertEqual(post.community, community)
        self.assertEqual(post.author, "Asha")
        self.assertEqual(post.category, "General")

    def test_dashboard_stats_use_real_data(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Weekly Meetup",
            description="Planning weekly community gatherings.",
            owner=user,
        )
        CommunityMembership.objects.create(community=community, user=user)
        CommunityPost.objects.create(
            community=community,
            title="Weekly meetup",
            author="Asha",
            category="Events",
            body="The next meetup is on Friday.",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard"))

        stats = {stat["label"]: stat["value"] for stat in response.context["stats"]}
        self.assertEqual(stats["Total Users"], "1")
        self.assertEqual(stats["Active Communities"], "1")
        self.assertEqual(stats["Posts Today"], "1")
        self.assertEqual(stats["Monthly Revenue"], "Rs. 0")

    def test_dashboard_uses_super_admin_template_for_superuser(self):
        admin = get_user_model().objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="StrongPass123!",
            first_name="Admin",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("hub:dashboard"))

        self.assertTemplateUsed(response, "admin-dashboard/super_admin.html")
        self.assertContains(response, "Super Admin")

    def test_dashboard_uses_user_template_for_member(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard"))

        self.assertTemplateUsed(response, "user-dashboard/sections/dashboard.html")
        self.assertContains(response, "Dashboard")
        self.assertNotContains(response, "Content & Posts")
        self.assertNotContains(response, "Create Post")
        self.assertContains(response, "aria-label=\"Open profile settings\"")
        self.assertContains(response, reverse("hub:logout"), count=2)
        self.assertContains(response, "Logout", count=1)
        self.assertNotContains(response, "Super Admin")

    def test_user_dashboard_sections_render_single_page(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "communities"}))

        self.assertTemplateUsed(response, "user-dashboard/sections/dashboard.html")
        self.assertEqual(response.context["active_page"], "communities")
        self.assertContains(response, "Communities")
        self.assertNotContains(response, "Activity Overview")

    def test_first_visit_dashboard_intros_are_persisted_per_user(self):
        user = self.create_member()
        self.client.force_login(user)
        section_flags = {
            "discover": "discover_started",
            "communities": "communities_started",
            "messages": "message_started",
            "studio": "studio_started",
        }

        for section, context_key in section_flags.items():
            url = reverse("hub:dashboard_section", kwargs={"section": section})
            first_response = self.client.get(url)
            self.assertFalse(first_response.context[context_key])

            started_response = self.client.get(f"{url}?started=1")
            self.assertTrue(started_response.context[context_key])

            self.client.logout()
            self.client.force_login(user)
            returning_response = self.client.get(url)
            self.assertTrue(returning_response.context[context_key])

        user.profile.refresh_from_db()
        onboarding = user.profile.personalization_preferences["dashboard_onboarding"]
        self.assertEqual(set(onboarding), set(section_flags))

    def test_creator_studio_aggregates_and_scopes_community_financials(self):
        owner = get_user_model().objects.create_user(
            username="creator@example.com",
            email="creator@example.com",
            password="StrongPass123!",
            first_name="Creator",
        )
        member_one = get_user_model().objects.create_user(
            username="member.one@example.com",
            email="member.one@example.com",
            password="StrongPass123!",
        )
        member_two = get_user_model().objects.create_user(
            username="member.two@example.com",
            email="member.two@example.com",
            password="StrongPass123!",
        )
        other_owner = get_user_model().objects.create_user(
            username="other@example.com",
            email="other@example.com",
            password="StrongPass123!",
        )
        first = Community.objects.create(
            name="Design Circle",
            topic="Design",
            description="Design community.",
            owner=owner,
        )
        second = Community.objects.create(
            name="Probe Club",
            topic="Research",
            description="Research community.",
            owner=owner,
        )
        unrelated = Community.objects.create(
            name="Unrelated Space",
            topic="Other",
            description="Not owned by the creator.",
            owner=other_owner,
        )
        for community in (first, second):
            CommunityMembership.objects.create(
                community=community,
                user=owner,
                role=CommunityMembership.OWNER,
            )
        CommunityMembership.objects.create(community=first, user=member_one)
        CommunityMembership.objects.create(community=second, user=member_one)
        CommunityMembership.objects.create(community=second, user=member_two)

        PaymentTransaction.objects.create(
            user=member_one,
            community=first,
            amount="125.00",
            status=PaymentTransaction.SUCCESS,
        )
        PaymentTransaction.objects.create(
            user=member_two,
            community=second,
            amount="200.00",
            status=PaymentTransaction.SUCCESS,
        )
        first_post = CommunityPost.objects.create(
            community=first,
            author_user=owner,
            author="Creator",
            title="Welcome",
            body="Welcome to Design Circle.",
            accept_donations=True,
        )
        unrelated_post = CommunityPost.objects.create(
            community=unrelated,
            author_user=owner,
            author="Creator",
            title="Unrelated post",
            body="This should not count in Creator Studio.",
            accept_donations=True,
        )
        PostDonation.objects.create(
            post=first_post,
            donor=member_one,
            amount="25.00",
            status=PostDonation.SUCCESS,
        )
        PostDonation.objects.create(
            post=unrelated_post,
            donor=member_two,
            amount="99.00",
            status=PostDonation.SUCCESS,
        )

        self.client.force_login(owner)
        studio = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "studio"}))

        self.assertEqual(studio.context["creator_member_count"], 2)
        self.assertEqual(studio.context["creator_membership_revenue"], Decimal("325.00"))
        self.assertEqual(studio.context["creator_donation_revenue"], Decimal("25.00"))
        self.assertEqual(studio.context["creator_earnings"], Decimal("350.00"))
        self.assertEqual(studio.context["creator_transaction_count"], 2)
        self.assertContains(studio, "Design Circle")
        self.assertContains(studio, "Probe Club")
        self.assertContains(studio, f"community={first.slug}")

        filtered = self.client.get(
            f"{reverse('hub:dashboard_section', kwargs={'section': 'payments'})}?community={second.slug}"
        )
        self.assertEqual(filtered.context["studio_payment_community"], second)
        self.assertEqual(filtered.context["studio_creator_transaction_total"], Decimal("200.00"))
        self.assertEqual(list(filtered.context["studio_creator_transactions"])[0].community_id, second.pk)

    def test_profile_dashboard_section_renders_current_users_profile(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "profile"}))

        self.assertTemplateUsed(response, "user-dashboard/sections/profile.html")
        self.assertEqual(response.context["profile_user"], user)
        self.assertContains(response, user.username)

    def test_profile_dropdown_quick_edit_updates_display_name_and_username(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard"))

        self.assertContains(response, 'id="sidebar-edit-profile-btn"')
        self.assertContains(response, 'id="profile-edit-modal"')

        response = self.client.post(
            reverse("hub:dashboard_section", kwargs={"section": "settings"}),
            {
                "action": "update_profile_quick",
                "first_name": "Asha",
                "username": "asha-photo",
            },
        )

        self.assertRedirects(response, reverse("hub:dashboard"), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Asha")
        self.assertEqual(user.username, "asha-photo")

    def test_communities_page_links_to_dedicated_create_page(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "communities"}))

        self.assertContains(response, reverse("hub:create_community"))
        self.assertContains(response, "Search communities")
        self.assertContains(response, "Joined Communities")
        self.assertNotContains(response, 'name="action" value="create_community"')

    def test_discover_search_filters_communities(self):
        user = self.create_member()
        matching = Community.objects.create(
            name="Nepal Photography Circle",
            topic="Creative",
            tags="photos, Kathmandu",
            visibility=Community.PUBLIC,
            description="A place to share and learn photography.",
            owner=user,
        )
        Community.objects.create(
            name="Gardening Club",
            topic="Wellness",
            visibility=Community.PUBLIC,
            description="Growing food and flowers together.",
            owner=user,
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("hub:dashboard_section", kwargs={"section": "discover"}),
            {"q": "photography"},
        )

        self.assertContains(response, 'id="discover-community-search"')
        self.assertContains(response, 'value="photography"')
        self.assertContains(response, matching.name)
        self.assertNotContains(response, "Gardening Club")

    def test_community_cards_link_to_detail_workspace(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "communities"}))

        self.assertContains(response, reverse("hub:community_detail", kwargs={"slug": community.slug}))
        self.assertContains(response, "View Community")
        self.assertNotContains(response, f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=settings#community-settings")
        self.assertNotContains(response, "Edit Settings")

    def test_joined_member_can_update_community_settings_from_detail(self):
        owner = get_user_model().objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="StrongPass123!",
        )
        user = self.create_member()
        community = Community.objects.create(
            name="Shared Design Club",
            topic="Design",
            visibility=Community.PUBLIC,
            description="A shared community.",
            owner=owner,
            membership_price="25.00",
        )
        CommunityMembership.objects.create(
            community=community,
            user=owner,
            role=CommunityMembership.OWNER,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.MEMBER,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "communities"}))
        settings_url = f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=settings#community-settings"
        self.assertNotContains(response, settings_url)
        self.assertNotContains(response, "Edit Settings")

        settings_response = self.client.get(f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=settings")
        self.assertContains(settings_response, "data-community-settings-section")
        self.assertContains(settings_response, "Community settings")

        update_response = self.client.post(
            reverse("hub:community_detail", kwargs={"slug": community.slug}),
            {
                "action": "update_community",
                "name": "Updated Design Club",
                "topic": "Creative Design",
                "location": "Kathmandu",
                "tags": "design, critiques",
                "interests": "Branding, workshops",
                "visibility": Community.PUBLIC,
                "description": "Updated shared community details.",
                "rules": "Keep feedback useful.",
                "membership_plan_name": "Design Access",
                "membership_price": "75.00",
                "membership_plan_description": "Updated access details.",
                "allow_member_posts": "on",
            },
        )

        self.assertRedirects(
            update_response,
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=settings",
            fetch_redirect_response=False,
        )
        community.refresh_from_db()
        self.assertEqual(community.name, "Updated Design Club")
        self.assertEqual(community.topic, "Creative Design")
        self.assertEqual(community.location, "Kathmandu")
        self.assertEqual(str(community.membership_price), "75.00")

    def test_non_member_cannot_update_community_settings(self):
        owner = get_user_model().objects.create_user(
            username="owner2@example.com",
            email="owner2@example.com",
            password="StrongPass123!",
        )
        user = self.create_member()
        community = Community.objects.create(
            name="Public Design Club",
            topic="Design",
            visibility=Community.PUBLIC,
            description="A public community.",
            owner=owner,
        )
        CommunityMembership.objects.create(
            community=community,
            user=owner,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("hub:community_detail", kwargs={"slug": community.slug}),
            {
                "action": "update_community",
                "name": "Unauthorized Rename",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Join this community before updating its settings.")
        community.refresh_from_db()
        self.assertEqual(community.name, "Public Design Club")

    def test_create_community_page_creates_owned_community(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.post(
            reverse("hub:create_community"),
            {
                "name": "Photography Nepal",
                "description": "A space for local photographers.",
            },
        )

        self.assertRedirects(
            response,
            reverse("hub:dashboard_section", kwargs={"section": "communities"}),
            fetch_redirect_response=False,
        )
        community = Community.objects.get(name="Photography Nepal")
        self.assertEqual(community.owner, user)
        self.assertEqual(community.topic, "General")
        self.assertEqual(community.visibility, Community.PUBLIC)
        self.assertTrue(community.allow_member_posts)
        self.assertEqual(community.membership_plan_name, "Community Access")
        self.assertEqual(str(community.membership_price), "0.00")
        self.assertTrue(
            CommunityMembership.objects.filter(
                community=community,
                user=user,
                role=CommunityMembership.OWNER,
            ).exists()
        )

    def test_create_community_page_renders_form_for_member(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:create_community"))

        self.assertTemplateUsed(response, "user-dashboard/sections/dashboard.html")
        self.assertEqual(response.context["active_page"], "communities")
        self.assertContains(response, "Back to Communities")
        self.assertContains(response, "The essentials")
        self.assertContains(response, "You can customize later.")
        self.assertContains(response, "data-community-create-form")
        self.assertContains(response, "Community name")
        self.assertContains(response, "What is your community about?")
        self.assertContains(response, "Community preview")
        self.assertContains(response, "data-community-preview-name")
        self.assertContains(response, "data-community-preview-description")
        self.assertNotContains(response, "Plan details")

    def test_community_detail_renders_modern_workspace_controls(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            location="Kathmandu",
            tags="photo walks, portraits",
            interests="Lighting, travel shoots",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            rules="Be respectful and credit creators.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial on advanced lighting techniques.",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("hub:community_detail", kwargs={"slug": community.slug}))

        self.assertTemplateUsed(response, "user-dashboard/community_detail.html")
        self.assertContains(response, "Photography Nepal")
        self.assertNotContains(response, "Back to Union")
        self.assertContains(response, "Back to home")
        self.assertContains(response, "Feed")
        self.assertContains(response, "Rooms")
        self.assertContains(response, "Links")
        self.assertNotContains(response, "Getting Started")
        self.assertContains(response, "Courses")
        self.assertContains(response, "Events")
        self.assertContains(response, "Members")
        self.assertContains(response, "Leaderboard")
        self.assertContains(response, "Create Post")
        self.assertContains(response, "Reply", count=1)
        self.assertContains(response, "Write a reply")
        self.assertContains(response, "Lighting tutorial")
        self.assertNotContains(response, "Change banner image")
        self.assertNotContains(response, "#photo walks")

        about_response = self.client.get(f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=about")
        self.assertContains(about_response, "#photo walks")
        self.assertNotContains(about_response, "Lighting tutorial")

        members_response = self.client.get(f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=members")
        self.assertContains(members_response, "Members")
        self.assertNotContains(members_response, "Lighting tutorial")

        settings_response = self.client.get(f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=settings")
        self.assertContains(settings_response, 'id="community-settings"')
        self.assertContains(settings_response, "data-community-settings-section")
        self.assertContains(settings_response, "Community settings")
        self.assertContains(settings_response, "Change banner image")
        self.assertNotContains(settings_response, "Lighting tutorial")

    def test_community_feed_does_not_render_trending_posts_panel(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial on advanced lighting techniques.",
            is_pinned=True,
        )
        self.client.force_login(user)

        response = self.client.get(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts"
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Trending posts")
        self.assertNotContains(response, "No trending posts yet.")

    def test_pinned_posts_are_ordered_above_unpinned_posts_in_feed(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        pinned_post = CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Pinned update",
            body="Important pinned update.",
            is_pinned=True,
        )
        unpinned_post = CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Recent update",
            body="Fresh but unpinned update.",
            is_pinned=False,
        )
        self.client.force_login(user)

        response = self.client.get(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts"
        )

        self.assertEqual(response.status_code, 200)
        posts = list(response.context["posts"])
        self.assertEqual(posts[0].id, pinned_post.id)
        self.assertEqual(posts[1].id, unpinned_post.id)

    def test_community_detail_ajax_create_post_publishes(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.post(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            {
                "action": "create_post",
                "title": "Golden hour walk",
                "body": "Meet at the square before sunset.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"Post published.")
        post = CommunityPost.objects.get(community=community)
        self.assertEqual(post.title, "Golden hour walk")
        self.assertEqual(post.body, "Meet at the square before sunset.")

    def test_community_detail_ajax_create_post_returns_validation_error(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.post(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            {"action": "create_post", "title": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, b"Share text, an image, or a video before posting.")
        self.assertFalse(CommunityPost.objects.filter(community=community).exists())

    def test_create_post_with_donation_toggle_enables_donations(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        self.client.post(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            {
                "action": "create_post",
                "title": "Fund the darkroom",
                "body": "Help us buy new gear.",
                "accept_donations": "on",
            },
        )

        post = CommunityPost.objects.get(community=community)
        self.assertTrue(post.accept_donations)

    def test_member_can_donate_to_post(self):
        owner = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=owner,
        )
        CommunityMembership.objects.create(
            community=community,
            user=owner,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=owner,
            author="Asha",
            title="Fund the darkroom",
            body="Help us buy new gear.",
            accept_donations=True,
        )

        donor = get_user_model().objects.create_user(
            username="donor@example.com",
            email="donor@example.com",
            password="StrongPass123!",
            first_name="Bina",
        )
        CommunityMembership.objects.create(community=community, user=donor)
        self.client.force_login(donor)

        response = self.client.post(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            {
                "action": "donate_post",
                "post_id": post.id,
                "amount": "150",
                "gateway": "esewa",
                "message": "Keep it up!",
            },
        )

        self.assertEqual(response.status_code, 302)
        donation = PostDonation.objects.get(post=post, donor=donor)
        self.assertEqual(donation.amount, Decimal("150"))
        self.assertEqual(donation.gateway, "esewa")
        self.assertEqual(donation.status, PostDonation.SUCCESS)
        self.assertTrue(
            PaymentTransaction.objects.filter(
                user=donor, reference_code=f"DON-{donation.id}"
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=owner, category=Notification.PAYMENT
            ).exists()
        )

    def test_donation_rejects_invalid_amount(self):
        owner = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=owner,
        )
        CommunityMembership.objects.create(
            community=community,
            user=owner,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=owner,
            author="Asha",
            title="Fund the darkroom",
            body="Help us buy new gear.",
            accept_donations=True,
        )
        donor = get_user_model().objects.create_user(
            username="donor@example.com",
            email="donor@example.com",
            password="StrongPass123!",
        )
        CommunityMembership.objects.create(community=community, user=donor)
        self.client.force_login(donor)

        self.client.post(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            {"action": "donate_post", "post_id": post.id, "amount": "0"},
        )

        self.assertFalse(PostDonation.objects.filter(post=post).exists())

    def test_author_cannot_donate_to_own_post(self):
        owner = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=owner,
        )
        CommunityMembership.objects.create(
            community=community,
            user=owner,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=owner,
            author="Asha",
            title="Fund the darkroom",
            body="Help us buy new gear.",
            accept_donations=True,
        )
        self.client.force_login(owner)

        self.client.post(
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            {"action": "donate_post", "post_id": post.id, "amount": "100"},
        )

        self.assertFalse(PostDonation.objects.filter(post=post).exists())

    def test_community_detail_create_image_post_renders_media(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        with TemporaryDirectory(dir=".", prefix="test-media-") as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
                {
                    "action": "create_post",
                    "title": "Golden hour frame",
                    "body": "",
                    "attachment_file": SimpleUploadedFile(
                        "golden-hour.png",
                        b"image-bytes",
                        content_type="image/png",
                    ),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            self.assertEqual(response.status_code, 200)
            post = CommunityPost.objects.get(community=community)
            self.assertTrue(post.attachment_file.name.startswith("community_attachments/"))

            page = self.client.get(reverse("hub:community_detail", kwargs={"slug": community.slug}))
            self.assertContains(page, "data-media-lightbox")
            self.assertContains(page, post.attachment_file.url)
            self.assertContains(page, 'alt="golden-hour.png"')

    def test_community_detail_create_video_post_renders_player(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        with TemporaryDirectory(dir=".", prefix="test-media-") as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
                {
                    "action": "create_post",
                    "title": "Behind the scenes",
                    "body": "",
                    "attachment_file": SimpleUploadedFile(
                        "behind-the-scenes.mp4",
                        b"video-bytes",
                        content_type="video/mp4",
                    ),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            self.assertEqual(response.status_code, 200)
            post = CommunityPost.objects.get(community=community)
            self.assertEqual(post.content_type, CommunityPost.VIDEO)

            page = self.client.get(reverse("hub:community_detail", kwargs={"slug": community.slug}))
            self.assertContains(page, "<video controls")
            self.assertContains(page, 'type="video/mp4"')
            self.assertContains(page, post.attachment_file.url)

    def test_community_detail_comment_accepts_image_attachment(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial.",
        )
        self.client.force_login(user)

        with TemporaryDirectory(dir=".", prefix="test-media-") as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
                {
                    "action": "create_comment",
                    "post_id": post.pk,
                    "body": "",
                    "attachment_file": SimpleUploadedFile(
                        "reply-frame.png",
                        b"reply-image-bytes",
                        content_type="image/png",
                    ),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"Reply added.")
            comment = post.comments.get()
            self.assertTrue(comment.attachment_file.name.startswith("community_attachments/"))

            page = self.client.get(reverse("hub:community_detail", kwargs={"slug": community.slug}))
            self.assertContains(page, comment.attachment_file.url)
            self.assertContains(page, 'alt="reply-frame.png"')

    def test_community_detail_post_reply_creates_comment(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial.",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("hub:community_detail", kwargs={"slug": community.slug}),
            {
                "action": "create_comment",
                "post_id": post.pk,
                "body": "This is helpful.",
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            fetch_redirect_response=False,
        )
        self.assertEqual(post.comments.count(), 1)
        self.assertEqual(post.comments.get().body, "This is helpful.")

    def test_community_detail_edit_post_updates_content(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial.",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse('hub:community_detail', kwargs={'slug': community.slug}),
            {
                'action': 'edit_post',
                'post_id': post.pk,
                'title': 'Lighting tutorial updated',
                'body': 'Updated post content.',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            fetch_redirect_response=False,
        )
        post.refresh_from_db()
        self.assertEqual(post.title, 'Lighting tutorial updated')
        self.assertEqual(post.body, 'Updated post content.')

    def test_community_detail_comment_delete_removes_comment(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial.",
        )
        comment = PostComment.objects.create(post=post, author=user, body="Nice post.")
        self.client.force_login(user)

        response = self.client.post(
            reverse('hub:community_detail', kwargs={'slug': community.slug}),
            {
                'action': 'delete_comment',
                'comment_id': comment.pk,
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            fetch_redirect_response=False,
        )
        self.assertFalse(PostComment.objects.filter(pk=comment.pk).exists())

    def test_community_detail_comment_reaction_can_be_added_and_removed(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        post = CommunityPost.objects.create(
            community=community,
            author_user=user,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial.",
        )
        comment = PostComment.objects.create(post=post, author=user, body="Nice post.")
        self.client.force_login(user)

        response = self.client.post(
            reverse('hub:community_detail', kwargs={'slug': community.slug}),
            {
                'action': 'react_comment',
                'comment_id': comment.pk,
                'reaction': CommentEngagement.LOVE,
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=posts",
            fetch_redirect_response=False,
        )
        self.assertTrue(
            CommentEngagement.objects.filter(comment=comment, user=user, engagement_type=CommentEngagement.LOVE).exists()
        )

        response = self.client.post(
            reverse('hub:community_detail', kwargs={'slug': community.slug}),
            {
                'action': 'react_comment',
                'comment_id': comment.pk,
                'reaction': CommentEngagement.LOVE,
            },
        )
        self.assertEqual(
            CommentEngagement.objects.filter(comment=comment, user=user, engagement_type=CommentEngagement.LOVE).count(),
            0,
        )

    def test_community_detail_reply_shows_comment_author_profile_image(self):
        owner = self.create_member()
        commenter = get_user_model().objects.create_user(
            username="biwash@example.com",
            email="biwash@example.com",
            password="StrongPass123!",
            first_name="Biwash",
        )
        UserProfile.objects.create(
            user=commenter,
            profile_image="profile_images/biwash.png",
        )
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=owner,
        )
        CommunityMembership.objects.create(
            community=community,
            user=owner,
            role=CommunityMembership.OWNER,
        )
        CommunityMembership.objects.create(community=community, user=commenter)
        post = CommunityPost.objects.create(
            community=community,
            author_user=owner,
            author="Asha",
            title="Lighting tutorial",
            body="Just uploaded the new tutorial.",
        )
        PostComment.objects.create(post=post, author=commenter, body="Hello from Biwash.")
        self.client.force_login(owner)

        response = self.client.get(reverse("hub:community_detail", kwargs={"slug": community.slug}))

        self.assertContains(response, "Biwash")
        self.assertContains(response, "Hello from Biwash.")
        self.assertContains(response, commenter.profile.profile_image.url)

    def test_owner_can_update_community_details_and_banner(self):
        user = self.create_member()
        community = Community.objects.create(
            name="Photography Nepal",
            topic="Photography",
            visibility=Community.PUBLIC,
            description="A space for local photographers.",
            owner=user,
        )
        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)
        cover_image = SimpleUploadedFile(
            "new-cover.png",
            b"new cover image",
            content_type="image/png",
        )

        response = self.client.post(
            reverse("hub:community_detail", kwargs={"slug": community.slug}),
            {
                "action": "update_community",
                "name": "Kathmandu Photo Club",
                "topic": "Street Photography",
                "location": "Lalitpur",
                "tags": "street, documentary",
                "interests": "Night shoots, editing",
                "visibility": Community.PRIVATE,
                "cover_image": cover_image,
                "description": "A focused club for city photographers.",
                "rules": "Share constructive feedback.",
                "membership_plan_name": "Studio Access",
                "membership_price": "99.00",
                "membership_plan_description": "Monthly meetups and critiques.",
                "allow_member_posts": "on",
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('hub:community_detail', kwargs={'slug': community.slug})}?tab=settings",
            fetch_redirect_response=False,
        )
        community.refresh_from_db()
        self.assertEqual(community.name, "Kathmandu Photo Club")
        self.assertEqual(community.topic, "Street Photography")
        self.assertEqual(community.location, "Lalitpur")
        self.assertEqual(community.tags, "street, documentary")
        self.assertEqual(community.interests, "Night shoots, editing")
        self.assertEqual(community.visibility, Community.PRIVATE)
        self.assertTrue(community.cover_image.name.startswith("community_covers/"))

    def test_joining_paid_community_records_payment_before_membership(self):
        owner = get_user_model().objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="StrongPass123!",
        )
        user = self.create_member()
        community = Community.objects.create(
            name="Paid Design Club",
            topic="Design",
            visibility=Community.PUBLIC,
            description="A paid design community.",
            owner=owner,
            membership_plan_name="Pro Design Access",
            membership_price="249.00",
            membership_plan_description="Paid access to design reviews.",
        )
        CommunityMembership.objects.create(
            community=community,
            user=owner,
            role=CommunityMembership.OWNER,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("hub:dashboard_section", kwargs={"section": "communities"}),
            {
                "action": "join_community",
                "community_id": community.pk,
            },
        )

        self.assertRedirects(
            response,
            reverse("hub:dashboard_section", kwargs={"section": "communities"}),
            fetch_redirect_response=False,
        )
        self.assertTrue(CommunityMembership.objects.filter(community=community, user=user).exists())
        transaction = PaymentTransaction.objects.get(user=user)
        self.assertEqual(str(transaction.amount), "249.00")
        self.assertEqual(transaction.gateway, PaymentTransaction.MANUAL)
        self.assertEqual(transaction.status, PaymentTransaction.SUCCESS)
        self.assertIn("Paid Design Club", transaction.notes)

    def test_profile_image_upload_updates_dashboard_avatar(self):
        user = self.create_member()
        self.client.force_login(user)

        image = SimpleUploadedFile(
            "avatar.png",
            b"fake image content",
            content_type="image/png",
        )
        response = self.client.post(
            reverse("hub:dashboard_section", kwargs={"section": "settings"}),
            {
                "action": "update_profile",
                "username": "member@example.com",
                "first_name": "Asha",
                "email": "member@example.com",
                "phone_number": "",
                "address": "",
                "interests": "",
                "theme_preference": UserProfile.THEME_SYSTEM,
                "bio": "",
                "profile_image": image,
            },
        )

        self.assertRedirects(
            response,
            reverse("hub:dashboard_section", kwargs={"section": "settings"}),
            fetch_redirect_response=False,
        )
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.profile_image.name.startswith("profile_images/"))

        dashboard = self.client.get(reverse("hub:dashboard"))
        self.assertContains(dashboard, user.profile.profile_image.url)

    def test_profile_settings_updates_username(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "settings"}))

        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'value="member@example.com"')

        response = self.client.post(
            reverse("hub:dashboard_section", kwargs={"section": "settings"}),
            {
                "action": "update_profile",
                "username": "asha-photo",
                "first_name": "Asha",
                "email": "member@example.com",
                "phone_number": "",
                "address": "",
                "interests": "Photography",
                "theme_preference": UserProfile.THEME_SYSTEM,
                "bio": "",
            },
        )

        self.assertRedirects(
            response,
            reverse("hub:dashboard_section", kwargs={"section": "settings"}),
            fetch_redirect_response=False,
        )
        user.refresh_from_db()
        self.assertEqual(user.username, "asha-photo")

    def test_messages_user_search_can_start_thread_by_username(self):
        user = self.create_member()
        recipient = get_user_model().objects.create_user(
            username="sunita",
            email="sunita@example.com",
            password="StrongPass123!",
            first_name="Sunita",
        )
        self.client.force_login(user)

        response = self.client.get(
            f"{reverse('hub:dashboard_section', kwargs={'section': 'messages'})}?user_q=suni"
        )

        self.assertContains(response, "Find people by username")
        self.assertContains(response, "@sunita")

        response = self.client.post(
            reverse("hub:dashboard_section", kwargs={"section": "messages"}),
            {
                "action": "start_message",
                "user_id": recipient.pk,
            },
        )

        thread = DirectMessageThread.objects.get()
        self.assertSetEqual({thread.participant1, thread.participant2}, {user, recipient})
        self.assertRedirects(
            response,
            f"{reverse('hub:dashboard_section', kwargs={'section': 'messages'})}?thread={thread.pk}",
            fetch_redirect_response=False,
        )

        api_response = self.client.get(reverse("hub:get_message_thread", kwargs={"thread_id": thread.pk}))
        self.assertEqual(api_response.json()["other_user_username"], "sunita")

    def test_messages_page_renders_compact_workspace(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "messages"}))

        self.assertContains(response, "messages-workspace")
        self.assertContains(response, "New chat")
        self.assertContains(response, "Write a message")
        self.assertNotContains(response, "Get Started with Chatting")

    def test_super_admin_can_create_user(self):
        admin = get_user_model().objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="StrongPass123!",
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("hub:dashboard"),
            {
                "action": "create_user",
                "first_name": "New Member",
                "username": "newadminmember",
                "email": "new@example.com",
                "password": "StrongPass123!",
                "is_active": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse('hub:dashboard_section', kwargs={'section': 'users'}),
            fetch_redirect_response=False,
        )
        self.assertTrue(get_user_model().objects.filter(email="new@example.com").exists())

    def test_super_admin_can_create_community_for_user(self):
        admin = get_user_model().objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="StrongPass123!",
        )
        owner = self.create_member()
        self.client.force_login(admin)

        response = self.client.post(
            reverse("hub:dashboard"),
            {
                "action": "create_community",
                "name": "Admin Created Space",
                "topic": "Operations",
                "visibility": Community.PUBLIC,
                "owner": owner.pk,
                "description": "A community created by the super admin.",
            },
        )

        self.assertRedirects(
            response,
            reverse('hub:dashboard_section', kwargs={'section': 'communities'}),
            fetch_redirect_response=False,
        )
        community = Community.objects.get(name="Admin Created Space")
        self.assertEqual(community.owner, owner)
        self.assertTrue(CommunityMembership.objects.filter(community=community, user=owner).exists())

    def test_contact_form_saves_message(self):
        response = self.client.post(
            reverse("hub:home"),
            {
                "full_name": "Maya Chen",
                "email": "maya@example.com",
                "message": "I want to learn more about Union.",
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('hub:home')}#contact",
            fetch_redirect_response=False,
        )
        self.assertEqual(ContactMessage.objects.count(), 1)
