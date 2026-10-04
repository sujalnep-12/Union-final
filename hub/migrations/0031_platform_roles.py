from django.db import migrations, models


def convert_legacy_community_admins(apps, schema_editor):
    """Community admin was an incorrect earlier role; keep those users as moderators."""
    CommunityMembership = apps.get_model("hub", "CommunityMembership")
    CommunityMembership.objects.filter(role="admin").update(role="moderator")

    UserProfile = apps.get_model("hub", "UserProfile")
    User = apps.get_model("auth", "User")
    UserProfile.objects.filter(user__is_staff=True, user__is_superuser=False).update(platform_role="admin")
    UserProfile.objects.filter(user__is_superuser=True).update(platform_role="owner")


class Migration(migrations.Migration):
    dependencies = [
        ("hub", "0030_payoutrequest_global_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="platform_role",
            field=models.CharField(
                choices=[
                    ("owner", "Platform owner"),
                    ("admin", "Platform admin"),
                    ("member", "Member"),
                ],
                default="member",
                help_text="Platform-wide role. Community roles are managed separately on CommunityMembership.",
                max_length=20,
            ),
        ),
        migrations.RunPython(convert_legacy_community_admins, migrations.RunPython.noop),
    ]
