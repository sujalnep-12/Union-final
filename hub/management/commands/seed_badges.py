from django.core.management.base import BaseCommand
from hub.models import Badge


BADGES = [
    {
        "name": "First Post",
        "slug": "first-post",
        "description": "Published your first post",
        "icon": "fa-pen-nib",
    },
    {
        "name": "Active Writer",
        "slug": "active-writer",
        "description": "Published 10 posts",
        "icon": "fa-feather",
    },
    {
        "name": "Prolific Creator",
        "slug": "prolific-creator",
        "description": "Published 50 posts",
        "icon": "fa-pen-fancy",
    },
    {
        "name": "Discussion Starter",
        "slug": "discussion-starter",
        "description": "Created a post with 10+ comments",
        "icon": "fa-comments",
    },
    {
        "name": "Popular Post",
        "slug": "popular-post",
        "description": "A post received 25+ likes",
        "icon": "fa-fire",
    },
    {
        "name": "Viral Post",
        "slug": "viral-post",
        "description": "A post received 100+ likes",
        "icon": "fa-bolt",
    },
    {
        "name": "First Follower",
        "slug": "first-follower",
        "description": "Gained your first follower",
        "icon": "fa-user-plus",
    },
    {
        "name": "Rising Star",
        "slug": "rising-star",
        "description": "Gained 50 followers",
        "icon": "fa-star",
    },
    {
        "name": "Community Builder",
        "slug": "community-builder",
        "description": "Created your first community",
        "icon": "fa-people-group",
    },
    {
        "name": "Helpful Member",
        "slug": "helpful-member",
        "description": "Received 10 upvotes on comments",
        "icon": "fa-hand-holding-heart",
    },
    {
        "name": "Poll Master",
        "slug": "poll-master",
        "description": "Created 5 polls",
        "icon": "fa-square-poll-vertical",
    },
    {
        "name": "Donor",
        "slug": "donor",
        "description": "Made your first donation",
        "icon": "fa-heart",
    },
    {
        "name": "Verified",
        "slug": "verified",
        "description": "Completed KYC verification",
        "icon": "fa-circle-check",
    },
    {
        "name": "Early Adopter",
        "slug": "early-adopter",
        "description": "Joined Union in its first month",
        "icon": "fa-rocket",
    },
    {
        "name": "Course Creator",
        "slug": "course-creator",
        "description": "Published your first course",
        "icon": "fa-graduation-cap",
    },
    {
        "name": "Event Host",
        "slug": "event-host",
        "description": "Created your first event",
        "icon": "fa-calendar-check",
    },
]


class Command(BaseCommand):
    help = "Seed initial badges into the database"

    def handle(self, *args, **options):
        created_count = 0
        for badge_data in BADGES:
            obj, created = Badge.objects.get_or_create(
                slug=badge_data["slug"],
                defaults=badge_data,
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {obj.name}")
            else:
                self.stdout.write(f"  Exists:  {obj.name}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {created_count} badge(s) created, {len(BADGES) - created_count} already existed."
        ))
