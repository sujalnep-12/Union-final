from django.db.models import Count, Q
from .models import Badge, UserBadge, CommunityPost, PostComment, UserFollow, PostPoll, PostDonation, Community, PostEngagement, CommentEngagement


def _award(user, badge_slug):
    badge = Badge.objects.filter(slug=badge_slug).first()
    if badge and not UserBadge.objects.filter(user=user, badge=badge).exists():
        UserBadge.objects.create(user=user, badge=badge)
        return badge
    return None


def check_and_award(user):
    awarded = []

    post_count = CommunityPost.objects.filter(author_user=user).count()

    if post_count >= 1:
        b = _award(user, "first-post")
        if b:
            awarded.append(b)

    if post_count >= 10:
        b = _award(user, "active-writer")
        if b:
            awarded.append(b)

    if post_count >= 50:
        b = _award(user, "prolific-creator")
        if b:
            awarded.append(b)

    discussion_starter = (
        CommunityPost.objects.filter(author_user=user)
        .annotate(comment_count=Count("comments"))
        .filter(comment_count__gte=10)
        .exists()
    )
    if discussion_starter:
        b = _award(user, "discussion-starter")
        if b:
            awarded.append(b)

    popular_post = (
        CommunityPost.objects.filter(author_user=user)
        .annotate(like_count=Count("engagements", filter=Q(engagements__engagement_type=PostEngagement.LIKE)))
        .filter(like_count__gte=25)
        .exists()
    )
    if popular_post:
        b = _award(user, "popular-post")
        if b:
            awarded.append(b)

    viral_post = (
        CommunityPost.objects.filter(author_user=user)
        .annotate(like_count=Count("engagements", filter=Q(engagements__engagement_type=PostEngagement.LIKE)))
        .filter(like_count__gte=100)
        .exists()
    )
    if viral_post:
        b = _award(user, "viral-post")
        if b:
            awarded.append(b)

    follower_count = UserFollow.objects.filter(followed=user).count()

    if follower_count >= 1:
        b = _award(user, "first-follower")
        if b:
            awarded.append(b)

    if follower_count >= 50:
        b = _award(user, "rising-star")
        if b:
            awarded.append(b)

    created_communities = Community.objects.filter(owner=user).count()
    if created_communities >= 1:
        b = _award(user, "community-builder")
        if b:
            awarded.append(b)

    comment_upvotes = (
        PostComment.objects.filter(author=user)
        .annotate(upvote_count=Count("engagements", filter=Q(engagements__engagement_type=CommentEngagement.LIKE)))
        .filter(upvote_count__gte=10)
        .exists()
    )
    if comment_upvotes:
        b = _award(user, "helpful-member")
        if b:
            awarded.append(b)

    poll_count = PostPoll.objects.filter(post__author_user=user).distinct().count()
    if poll_count >= 5:
        b = _award(user, "poll-master")
        if b:
            awarded.append(b)

    donation_count = PostDonation.objects.filter(donor=user).count()
    if donation_count >= 1:
        b = _award(user, "donor")
        if b:
            awarded.append(b)

    if hasattr(user, "profile") and user.profile.kyc_verified:
        b = _award(user, "verified")
        if b:
            awarded.append(b)

    course_count = CommunityPost.objects.filter(
        author_user=user, category__iexact="course"
    ).count()
    if course_count >= 1:
        b = _award(user, "course-creator")
        if b:
            awarded.append(b)

    event_count = CommunityPost.objects.filter(
        author_user=user, content_type=CommunityPost.EVENT
    ).count()
    if event_count >= 1:
        b = _award(user, "event-host")
        if b:
            awarded.append(b)

    return awarded
