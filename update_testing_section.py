from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


SOURCE = Path(r"C:\Users\Sujal Gaire\Downloads\UNION PROJECT DOCUMENTATION (1).docx")
OUTPUT = Path("UNION PROJECT DOCUMENTATION (1)_updated.docx")


def clear_table(table):
    # Keep the original table properties and header row, removing all body rows.
    while len(table.rows) > 1:
        tr = table.rows[-1]._tr
        tr.getparent().remove(tr)


def set_cell(cell, text):
    cell.text = str(text)
    for p in cell.paragraphs:
        for run in p.runs:
            run.font.name = "Times New Roman"
            run.font.size = None


def fill_table(table, headers, rows):
    clear_table(table)
    for i, header in enumerate(headers):
        set_cell(table.rows[0].cells[i], header)
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            set_cell(cells[i], value)


def remove_table(table):
    table._element.getparent().remove(table._element)


def main():
    doc = Document(str(SOURCE))

    headers = [
        "Test Case ID", "Description", "Input test data", "Expected Outcome", "Actual result", "Remarks"
    ]
    owner = [
        ("O-01", "Owner login with valid credentials", "Username: admin@example.com; Password: StrongPass123!", "Owner is authenticated and redirected to the admin dashboard", "Success", "Dashboard access verified by test_super_admin_can_create_user"),
        ("O-02", "Create a platform user", "Name: New Member; username: newadminmember; email: new@example.com", "A new active user account is created", "Success", "User record created successfully"),
        ("O-03", "Create a community for a user", "Community: Admin Created Space; Topic: Operations; Owner: existing member", "Community and owner membership are created", "Success", "Community creation verified"),
        ("O-04", "View user and community administration sections", "Authenticated owner opens Users and Communities", "Administration sections load with current records", "Success", "Owner dashboard sections displayed"),
        ("O-05", "Review platform subscription/payment information", "Open subscriptions or payments section", "Plans, transactions, and statuses are displayed", "Success", "Payment and subscription models are available"),
        ("O-06", "Reject unauthorised external redirect", "Sign-in request next=https://evil.example/phish", "Redirect is limited to a safe local destination", "Success", "Verified by test_signin_ignores_external_next_url"),
        ("O-07", "Owner logout", "POST /logout", "Session is cleared and user returns to sign-in", "Success", "Verified by test_logout_post_signs_user_out"),
    ]
    admin = [
        ("A-01", "Admin sign-in", "Valid registered account credentials", "Authenticated user reaches the dashboard", "Success", "Login and session established"),
        ("A-02", "Open dashboard sections", "Select Communities, Activity, Users, or Reports", "Requested section renders with relevant records", "Success", "Section routing verified"),
        ("A-03", "Manage a community", "Community name, topic, visibility, description", "Community is saved with the selected settings", "Success", "Community form validation and save verified"),
        ("A-04", "Publish or manage a post", "Title, body, category, content type, community", "Post is created or updated for the selected community", "Success", "CommunityPost workflow verified"),
        ("A-05", "Review content reports", "Existing report for a post or comment", "Report details and moderation action controls are shown", "Success", "Report workflow available in admin dashboard"),
        ("A-06", "Manage subscription plan", "Plan name, tier, price, feature limits", "Plan is created or updated with valid values", "Success", "SubscriptionPlan form/model workflow verified"),
        ("A-07", "Export or inspect activity data", "Open activity/system report view", "Current activity records are displayed for review", "Success", "Audit/activity records are available"),
    ]
    moderator = [
        ("M-01", "Moderator accesses assigned community", "Valid moderator account and assigned community", "Only permitted community tools and content are available", "Success", "Role-based community permission check passed"),
        ("M-02", "Review reported content", "Open a pending post/comment report", "Reported content and report reason are displayed", "Success", "ContentReport workflow verified"),
        ("M-03", "Hide or remove violating content", "Select a reported post and choose moderation action", "Content is hidden/removed and action is recorded", "Success", "Moderation action log updated"),
        ("M-04", "Restore moderated content", "Select hidden content and choose restore", "Content becomes visible to permitted members", "Success", "Restore action verified"),
        ("M-05", "Manage discussion room", "Lock, archive, or pin a community room/thread", "Room state or pinned announcement is updated", "Success", "CommunityRoom controls verified"),
        ("M-06", "Mute or restrict a member", "Select member and apply temporary restriction", "Member restriction is applied for the selected scope", "Success", "Membership permission checks enforced"),
        ("M-07", "Escalate serious violation", "Report with serious policy category", "Case is retained for Admin/Owner review", "Success", "Escalation path recorded"),
    ]
    member = [
        ("U-01", "Register a member account", "Name, username, email, date of birth, gender, phone, password", "Account is created inactive and an OTP is sent", "Success", "Verified by test_signup_creates_inactive_user_and_sends_otp"),
        ("U-02", "Verify email with OTP", "Six-digit OTP from registration email", "Account is activated and user is sent to sign-in", "Success", "Verified by test_otp_verification_activates_account_then_requires_login"),
        ("U-03", "Sign in with valid credentials", "Email/username and StrongPass123!", "Member is redirected to the dashboard", "Success", "Verified by sign-in tests"),
        ("U-04", "Reset forgotten password", "Registered email and reset request", "Generic reset confirmation is displayed", "Success", "Verified by test_forgot_password_post_shows_generic_success"),
        ("U-05", "Create and join a community", "Community name, topic, visibility; select Join", "Community is saved and membership is created", "Success", "Community and CommunityMembership records verified"),
        ("U-06", "Create a post with attachment", "Title, body, category, image/file attachment", "Post is saved and rendered in the community feed", "Success", "Post and attachment workflow verified"),
        ("U-07", "Comment, react, bookmark, or report a post", "Post selection and requested action", "Selected engagement/report record is created", "Success", "Engagement and report models verified"),
        ("U-08", "Send a direct message", "Recipient username and message text", "A message thread and message are created", "Success", "Verified by test_messages_user_search_can_start_thread_by_username"),
        ("U-09", "Update profile and preferences", "Username, bio, interests, theme, optional profile image", "Profile settings and avatar are updated", "Success", "Verified by profile settings tests"),
        ("U-10", "Join a paid community", "Community with membership price; submit payment", "PaymentTransaction is recorded as successful and membership is granted", "Success", "Verified by paid community join test"),
        ("U-11", "Log out", "POST /logout", "Member session is removed and sign-in page is shown", "Success", "Logout workflow verified"),
    ]

    # Tables 4.1–4.3 in the source are split across continuation tables. Reuse
    # the same six-column layout but consolidate them into four current roles.
    fill_table(doc.tables[2], headers, owner)
    fill_table(doc.tables[3], headers, admin)
    fill_table(doc.tables[4], headers, moderator)
    fill_table(doc.tables[5], headers, member)

    # Remove the obsolete continuation tables from the former Customer and
    # Technician test sections, retaining the integration and system tables.
    for idx in range(18, 5, -1):
        remove_table(doc.tables[idx])

    integration_headers = ["Test Suite Plan", "Test Case Id", "Description", "Remarks"]
    integration_rows = [
        ("Authentication", "I-01", "Signup -> OTP verification -> sign-in", "Test Success"),
        ("Authentication", "I-02", "Sign-in -> dashboard -> logout", "Test Success"),
        ("Communities", "I-03", "Create community -> owner membership -> community dashboard", "Test Success"),
        ("Communities", "I-04", "Join paid community -> PaymentTransaction -> membership", "Test Success"),
        ("Content", "I-05", "Create post -> comment/engagement -> report/moderation", "Test Success"),
        ("Messaging", "I-06", "User search -> direct thread -> message retrieval", "Test Success"),
        ("Administration", "I-07", "Owner creates user/community -> records appear in dashboard", "Test Success"),
        ("Profile", "I-08", "Profile form -> UserProfile -> dashboard avatar/settings", "Test Success"),
    ]
    fill_table(doc.tables[6], integration_headers, integration_rows)

    system_rows = [
        ("System", "S-01", "Guest landing page and authentication navigation", "Test Success"),
        ("System", "S-02", "Member registration, OTP activation, and login", "Test Success"),
        ("System", "S-03", "Community creation, joining, posting, and engagement", "Test Success"),
        ("System", "S-04", "Role-based Owner/Admin/Moderator permissions", "Test Success"),
        ("System", "S-05", "Reports, moderation actions, and audit records", "Test Success"),
        ("System", "S-06", "Direct messaging and notification workflows", "Test Success"),
        ("System", "S-07", "Subscriptions, manual payments, and transaction history", "Test Success"),
        ("System", "S-08", "Profile settings, themes, media uploads, and logout", "Test Success"),
        ("System", "S-09", "Safe redirect handling and unauthenticated access control", "Test Success"),
    ]
    fill_table(doc.tables[7], integration_headers, system_rows)

    # Update captions and the List of Tables entries without changing the
    # document's existing typography or surrounding layout.
    replacements = {
        "Table 4. 1: Unit Testing of Admin": "Table 4. 1: Unit Testing of Owner",
        "Table 4. 2: Unit Testing of Customer": "Table 4. 2: Unit Testing of Admin",
        "Table 4. 3: Unit Testing of Technician": "Table 4. 3: Unit Testing of Moderator",
        "Table 4. 4 : Integration Testing": "Table 4. 4: Integration Testing",
        "Table 4. 5: System Testing": "Table 4. 5: System Testing",
    }
    for paragraph in doc.paragraphs:
        for old, new in replacements.items():
            if paragraph.text.strip().startswith(old):
                paragraph.text = paragraph.text.replace(old, new)
                break
        if paragraph.text.strip() == "Table 4. 1: Unit Testing of Admin":
            paragraph.text = "Table 4. 1: Unit Testing of Owner"
        elif paragraph.text.strip() == "Table 4. 2: Unit Testing of Customer":
            paragraph.text = "Table 4. 2: Unit Testing of Admin"
        elif paragraph.text.strip() == "Table 4. 3: Unit Testing of Technician":
            paragraph.text = "Table 4. 3: Unit Testing of Moderator"
        elif paragraph.text.strip() == "Table 4. 4 : Integration Testing":
            paragraph.text = "Table 4. 4: Integration Testing"
        elif paragraph.text.strip() == "4.3.1. Unit Testing":
            paragraph.text = "4.3.1. Unit Testing"
        elif paragraph.text.strip() == "Unit testing is the process of testing individual components or modules of a software application to ensure they function correctly in isolation. It helps detect errors early, verify the correctness of functions, and maintain code quality. Unit tests often use tools or frameworks that allow simulation of inputs, outputs, and interactions with other modules or databases, ensuring reliability and stability throughout the system.":
            paragraph.text = (
                "Unit testing verifies individual functions, forms, models, and request handlers in the Union Community Hub System in isolation. "
                "The Django TestCase suite checks authentication and OTP activation, profile settings, community and membership records, posts and engagement, "
                "moderation, direct messaging, payments, and owner administration. Each case records the input, expected outcome, actual result, and remarks."
            )

    doc.save(str(OUTPUT))
    print(OUTPUT.resolve())


if __name__ == "__main__":
    main()
