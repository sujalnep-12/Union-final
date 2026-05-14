from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Community, CommunityMembership, CommunityPost, ContactMessage, UserProfile


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
            reverse("hub:dashboard_section", kwargs={"section": "content"}),
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

        self.assertTemplateUsed(response, "dashboard/super_admin.html")
        self.assertContains(response, "Super Admin")

    def test_dashboard_uses_user_template_for_member(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard"))

        self.assertTemplateUsed(response, "dashboard/user.html")
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "View profile picture")
        self.assertContains(response, 'id="profilePreview"')
        self.assertContains(response, reverse("hub:logout"), count=1)
        self.assertContains(response, "Logout", count=1)
        self.assertNotContains(response, "Super Admin")

    def test_user_dashboard_sections_render_single_page(self):
        user = self.create_member()
        self.client.force_login(user)

        response = self.client.get(reverse("hub:dashboard_section", kwargs={"section": "communities"}))

        self.assertTemplateUsed(response, "dashboard/user.html")
        self.assertEqual(response.context["active_page"], "communities")
        self.assertContains(response, "Communities")
        self.assertNotContains(response, "Activity Overview")

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
            f"{reverse('hub:dashboard')}#users",
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
            f"{reverse('hub:dashboard')}#communities",
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
