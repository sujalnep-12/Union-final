from django.core.management.base import BaseCommand

from hub.models import Community, CommunityRoom


DEFAULT_ROOMS = [
    {"name": "Announcements", "room_type": "announcements", "layout": "blank", "icon_class": "fa-solid fa-bullhorn", "color": "purple", "display_order": 0},
    {"name": "Chat", "room_type": "chat", "layout": "chat", "icon_class": "fa-solid fa-comment-dots", "color": "blue", "display_order": 1},
    {"name": "Topics", "room_type": "topics", "layout": "forum", "icon_class": "fa-solid fa-layer-group", "color": "indigo", "display_order": 2},
    {"name": "Upcoming Events", "room_type": "events", "layout": "event", "icon_class": "fa-solid fa-calendar-days", "color": "red", "display_order": 3},
    {"name": "Resources & Downloads", "room_type": "resources", "layout": "resource", "icon_class": "fa-solid fa-folder-open", "color": "green", "display_order": 4},
    {"name": "Certifications", "room_type": "certifications", "layout": "blank", "icon_class": "fa-solid fa-award", "color": "yellow", "display_order": 5},
    {"name": "Networking Hub", "room_type": "networking", "layout": "blank", "icon_class": "fa-solid fa-globe", "color": "cyan", "display_order": 6},
    {"name": "Jobs & Internships", "room_type": "jobs", "layout": "job_board", "icon_class": "fa-solid fa-briefcase", "color": "orange", "display_order": 7},
    {"name": "Live Sessions", "room_type": "live", "layout": "blank", "icon_class": "fa-solid fa-video", "color": "rose", "display_order": 8},
]


class Command(BaseCommand):
    help = "Seed default rooms for all communities that have none."

    def handle(self, *args, **options):
        communities = Community.objects.all()
        total_created = 0
        for community in communities:
            existing = set(community.rooms.values_list("name", flat=True))
            for room_data in DEFAULT_ROOMS:
                if room_data["name"] not in existing:
                    CommunityRoom.objects.create(
                        community=community,
                        is_default=True,
                        **room_data,
                    )
                    total_created += 1
        self.stdout.write(self.style.SUCCESS(f"Created {total_created} default rooms."))
