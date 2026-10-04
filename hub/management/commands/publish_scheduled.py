from django.core.management.base import BaseCommand
from django.utils import timezone
from hub.models import CommunityPost


class Command(BaseCommand):
    help = "Publish scheduled posts whose publish_at has passed"

    def handle(self, *args, **options):
        now = timezone.now()
        posts = CommunityPost.objects.filter(publish_at__lte=now, is_pinned=False)
        count = posts.count()
        for post in posts:
            post.publish_at = None
            post.save(update_fields=["publish_at"])
        self.stdout.write(self.style.SUCCESS(f"Published {count} scheduled post(s)"))
