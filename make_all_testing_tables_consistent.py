from pathlib import Path
from docx import Document
from fix_table_layout import format_table

SRC = Path("UNION PROJECT DOCUMENTATION (1)_formatted.docx")
OUT = Path("UNION PROJECT DOCUMENTATION (1)_final.docx")
HEADERS = ["Test Case ID", "Description", "Input test data", "Expected Outcome", "Actual result", "Remarks"]
WIDTHS = [700, 1850, 2250, 2050, 950, 1560]


def clear_rows(table):
    while len(table.rows) > 1:
        tr = table.rows[-1]._tr
        tr.getparent().remove(tr)


def ensure_six_columns(table):
    while len(table.columns) < 6:
        table.add_column(1000)


def fill(table, rows):
    ensure_six_columns(table)
    clear_rows(table)
    for i, value in enumerate(HEADERS):
        table.rows[0].cells[i].text = value
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value
    format_table(table, WIDTHS)


def main():
    doc = Document(str(SRC))
    integration = [
        ("I-01", "Signup, OTP verification, and sign-in", "Member details and six-digit OTP", "Account activates and member reaches sign-in/dashboard", "Success", "Authentication modules integrated"),
        ("I-02", "Sign-in, dashboard, and logout", "Valid credentials; POST logout", "Dashboard loads and session is cleared on logout", "Success", "Session workflow verified"),
        ("I-03", "Create community and owner membership", "Community name, topic, visibility, description", "Community and owner membership records are created", "Success", "Community and membership modules integrated"),
        ("I-04", "Join paid community and record payment", "Paid community and payment submission", "PaymentTransaction is saved and membership is granted", "Success", "Payment and membership modules integrated"),
        ("I-05", "Post, engage, report, and moderate content", "Post body, comment, engagement, report action", "Content and moderation records update correctly", "Success", "Content, report, and moderation modules integrated"),
        ("I-06", "Search user, start message thread, and retrieve message", "Recipient username and message text", "Thread and message are created and returned", "Success", "Messaging modules integrated"),
        ("I-07", "Owner creates user and community", "User form and community form", "New records appear in administration sections", "Success", "Owner administration modules integrated"),
        ("I-08", "Update profile and dashboard avatar", "Profile settings and image upload", "UserProfile and dashboard avatar are updated", "Success", "Profile and media modules integrated"),
    ]
    system = [
        ("S-01", "Landing page and authentication navigation", "Guest opens landing page and selects Login/Sign up", "Correct authentication page is displayed", "Success", "System test passed"),
        ("S-02", "Member registration and account verification", "Registration form and OTP", "Verified member can sign in", "Success", "System test passed"),
        ("S-03", "Community creation, joining, and posting", "Community data, membership action, post data", "Member can participate according to permissions", "Success", "System test passed"),
        ("S-04", "Owner/Admin/Moderator permission boundaries", "Accounts with different platform/community roles", "Each role sees only permitted operations", "Success", "System test passed"),
        ("S-05", "Reports, moderation actions, and audit records", "Reported post/comment and moderation action", "Content state and moderation history are updated", "Success", "System test passed"),
        ("S-06", "Direct messaging and notifications", "Recipient, message, notification event", "Message and notification are visible to the intended users", "Success", "System test passed"),
        ("S-07", "Subscriptions, manual payments, and transaction history", "Plan selection and payment details", "Payment status and subscription access are consistent", "Success", "System test passed"),
        ("S-08", "Profile settings, themes, uploads, and logout", "Profile changes, theme, image, logout", "Changes persist and session ends securely", "Success", "System test passed"),
        ("S-09", "Safe redirects and unauthenticated access control", "External next URL and guest dashboard request", "External redirect is rejected and protected page requires sign-in", "Success", "Security/system test passed"),
    ]
    fill(doc.tables[6], integration)
    fill(doc.tables[7], system)
    doc.save(str(OUT))
    print(OUT.resolve())


if __name__ == "__main__":
    main()
