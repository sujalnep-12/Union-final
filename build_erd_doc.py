import os
import sys
import json
from pathlib import Path

# Use the bundled document runtime for python-docx while retaining the project
# virtual environment for Django model introspection.
sys.path.insert(0, r"C:\Users\Sujal Gaire\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages")
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

OUT = Path("D:/unial final/ERD_new_system_specification.docx")
metadata = json.loads(Path("D:/unial final/erd_metadata.json").read_text(encoding="utf-8"))
model_map = {m["name"]: m for m in metadata["models"]}
all_entity_names = list(model_map)
relation_rows = [(r["source"], r["field"], r["target"], r["cardinality"]) for r in metadata["relations"]]
field_count = sum(len(m["fields"]) for m in metadata["models"])
fk_count = sum(1 for r in relation_rows if r[3] in {"N:1", "1:1"})
o2o_count = sum(1 for r in relation_rows if r[3] == "1:1")
m2m_count = sum(1 for r in relation_rows if r[3] == "N:M")


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def set_table_widths(table, widths):
    table.autofit = False
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(widths)))
    tbl_w.set(qn("w:type"), "dxa")
    grid = tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(width))
            tc_w.set(qn("w:type"), "dxa")
            set_cell_margins(cell)


def add_table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    hdr = table.rows[0]
    set_repeat_table_header(hdr)
    for i, text in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = text
        set_cell_shading(cell, "E8EEF5")
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(31, 77, 120)
    for row_data in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row_data):
            cells[i].text = value
            cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for p in cells[i].paragraphs:
                p.paragraph_format.space_after = Pt(2)
                for run in p.runs:
                    run.font.size = Pt(9)
    set_table_widths(table, widths)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


doc = Document()
section = doc.sections[0]
section.top_margin = Inches(0.75)
section.bottom_margin = Inches(0.7)
section.left_margin = Inches(0.8)
section.right_margin = Inches(0.8)

styles = doc.styles
normal = styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(10.5)
normal.paragraph_format.space_after = Pt(5)
normal.paragraph_format.line_spacing = 1.15
for name, size, color, before, after in [
    ("Title", 23, "1F4D78", 0, 8),
    ("Heading 1", 16, "2E74B5", 16, 8),
    ("Heading 2", 13, "2E74B5", 12, 6),
    ("Heading 3", 11, "1F4D78", 8, 4),
]:
    st = styles[name]
    st.font.name = "Calibri"
    st.font.size = Pt(size)
    st.font.color.rgb = RGBColor.from_string(color)
    st.font.bold = True
    st.paragraph_format.space_before = Pt(before)
    st.paragraph_format.space_after = Pt(after)

title = doc.add_paragraph(style="Title")
title.alignment = WD_ALIGN_PARAGRAPH.LEFT
title.add_run("Entity Relationship Diagram Specification\n").bold = True
sub = title.add_run("Current system baseline - requested scope excluded")
sub.font.size = Pt(12)
sub.font.color.rgb = RGBColor(89, 89, 89)

p = doc.add_paragraph()
p.add_run("Purpose. ").bold = True
p.add_run("This document defines the entities, attributes, and relationships that must appear in the replacement ER diagram. It is derived from the current Django model metadata and excludes the AI portion requested for removal.")

doc.add_heading("1. Final scope and counts", level=1)
add_table(doc, ["Metric", "Final value"], [
    ("Entities", f"{len(all_entity_names)} (including Django User; requested AI scope excluded)"),
    ("Attributes / database fields", str(field_count)),
    ("Foreign-key relationship fields", str(fk_count)),
    ("One-to-one relationship fields", str(o2o_count)),
    ("Many-to-many declarations", str(m2m_count)),
    ("Total relationship declarations", str(len(relation_rows))),
    ("AI entities and AI-specific fields", "Removed from the diagram specification"),
], [3000, 6360])

doc.add_heading("1A. Canonical role architecture", level=1)
doc.add_paragraph("The replacement ERD uses exactly four application roles: Owner, Admin, Moderator, and Member. Roles are values and assignments connected to User; they are not four separate User entities.")
add_table(doc, ["Canonical role", "Meaning in the ERD", "Primary storage / connection"], [
    ("Owner", "Highest authority for a community or platform scope", "UserProfile.platform_role or CommunityRole + CommunityRoleAssignment"),
    ("Admin", "Administrative management and moderation authority", "UserProfile.platform_role or CommunityRole + CommunityRoleAssignment"),
    ("Moderator", "Content, room, and member moderation authority", "CommunityRole + CommunityRoleAssignment"),
    ("Member", "Standard participating user", "CommunityMembership and CommunityRoleAssignment"),
], [1900, 3600, 3860])
doc.add_paragraph("The ERD must show CommunityRole and CommunityRoleAssignment as the normalized role system, with the four seeded role values Owner, Admin, Moderator, and Member. SubscriptionPlan and SubscriptionRole remain billing/access entities, not additional application roles.")

doc.add_heading("2. Original diagram: replacement map", level=1)
rows = [
    ("Admin", "Remove as a standalone entity", "Represent administrator access with UserProfile.platform_role, CommunityMembership.role, and CommunityRole."),
    ("User", "User + UserProfile", "Keep authentication in User and profile/account data in UserProfile."),
    ("Community", "Community", "Keep, but use the full Community model."),
    ("Community plans", "SubscriptionPlan + SubscriptionTierLimits + SubscriptionFeatureAccess", "Replace the single plan node with the actual plan and access models."),
    ("Content", "CommunityPost", "Add room, author, approval, moderation, attachment, and scheduling fields."),
    ("Community subscription", "CommunitySubscriptionAccess + UserSubscription", "Separate community access from a user's purchased subscription."),
    ("Payment", "PaymentTransaction + PostDonation + PayoutRequest + CommunityBoost", "Separate member payments, donations, creator payouts, and boosts."),
    ("Interaction", "PostComment + PostEngagement + CommentEngagement + Notification", "Separate comments, reactions, and notifications."),
    ("Chatroom", "CommunityRoom", "Use CommunityRoom for community-scoped rooms."),
    ("Chatmessages", "DirectMessageThread + DirectMessage", "Use thread and message entities for direct messaging."),
    ("Membership plans", "SubscriptionPlan + SubscriptionRole + CommunitySubscriptionAccess", "Use the subscription/access models instead of a duplicate membership-plan node."),
    ("Report", "ContentReport", "Add reporter, reported user, community, post, review, status, and resolution fields."),
]
add_table(doc, ["Original node", "Replacement", "Required change"], rows, [1900, 3000, 4460])

doc.add_heading("3. Entity and attribute inventory", level=1)
doc.add_paragraph("Every row below represents one entity that must appear in the new ER diagram. Fields in parentheses show the Django field type; relationship fields include their target entity. The AI entity and AI-specific fields are intentionally absent.")

groups = {
    "Authentication and community": {"User", "UserProfile", "Community", "CommunityMembership", "CommunityRole", "CommunityRoleAssignment", "CommunityRoom", "CommunityRoomMembership", "CommunityLink", "RoomRolePermission", "RoomAccessGroup", "RoomAccessGroupMember"},
    "Content and engagement": {"CommunityPost", "PostComment", "PostEngagement", "CommentEngagement", "HiddenPost", "BookmarkedPost", "UserFollow", "UserReputation", "Badge", "UserBadge", "PostAward", "PostPoll", "PollOption", "PollVote"},
    "Subscriptions and finance": {"SubscriptionPlan", "SubscriptionTierLimits", "SubscriptionRole", "SubscriptionFeatureAccess", "CommunitySubscriptionAccess", "SubscriptionActivityLog", "UserSubscription", "PaymentTransaction", "PostDonation", "PayoutRequest", "CommunityBoost"},
    "Messaging and platform records": {"DirectMessageThread", "DirectMessage", "Notification", "AuditLog", "ContactMessage"},
    "Events and learning": {"CommunityEvent", "EventRSVP", "Course", "Lesson", "LessonProgress", "Certificate"},
    "Community configuration": {"CommunityProfileFieldDefinition", "CommunityProfileFieldValue", "OnboardingFlow", "InviteLink", "InterestTag", "Announcement", "EmailBroadcast", "Integration", "SlashCommand"},
    "Voice, stage, and community assets": {"RoomVoiceSession", "StageSpeakerQueue", "CustomEmoji"},
    "Moderation and trust": {"RulesAcknowledgment", "ContentActionLog", "ContentApproval", "ContentReport", "ProfanityRule", "ModerationAction", "AutoRoleRule"},
}

for group, names in groups.items():
    doc.add_heading(group, level=2)
    rows = []
    for name in sorted(names):
        m = model_map[name]
        fields = [f"{f['name']} ({f['type']}" + (f" -> {f['target']})" if f['target'] else ")") for f in m['fields']]
        rels = [f"{r['field']} -> {r['target']} ({r['cardinality']})" for r in m['relations']]
        rows.append((name, ", ".join(fields), "; ".join(rels) if rels else "-"))
    add_table(doc, ["Entity", "Attributes / fields", "Relationships declared on entity"], rows, [1800, 5000, 2560])

doc.add_heading("4. Relationship inventory", level=1)
doc.add_paragraph("The relationship table is the authoritative connection list for drawing crow's-foot connectors. Foreign keys are N:1 from the entity in the first column to the target in the third column; one-to-one fields are marked 1:1; many-to-many fields are marked N:M.")
rel_rows = [(a, field, b, cardinality) for a, field, b, cardinality in sorted(relation_rows)]
add_table(doc, ["Source entity", "Field", "Target entity", "Cardinality"], rel_rows, [2300, 2500, 3000, 1560])

doc.add_heading("5. Diagram rules", level=1)
rules = [
    "Do not draw Admin as a table; show it as a role attribute/role entity attached to User and community membership.",
    "Do not draw a generic Interaction table; use the separate engagement and comment entities.",
    "Do not combine CommunityRoom and DirectMessageThread; they represent different messaging scopes.",
    "Do not combine PaymentTransaction, PostDonation, PayoutRequest, and CommunityBoost; they have different owners, states, and workflows.",
    "Show SubscriptionPlan separately from UserSubscription and CommunitySubscriptionAccess.",
    "Exclude the requested AI scope from the ER diagram.",
    "Use the exact entity and field names in this document as the labels in the final ER diagram.",
]
for item in rules:
    p = doc.add_paragraph(style="List Bullet")
    p.add_run(item)

doc.add_heading("6. Source and change note", level=1)
doc.add_paragraph("Source of truth: hub/models.py in the current project. This specification is a diagram deliverable only; it does not modify the application database.")

footer = section.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
footer_run = footer.add_run("ERD specification | Requested scope excluded | Generated from current Django models")
footer_run.font.size = Pt(8)
footer_run.font.color.rgb = RGBColor(100, 100, 100)

doc.core_properties.title = "Entity Relationship Diagram Specification"
doc.core_properties.subject = "Complete ERD entity, attribute, and relationship inventory"
doc.core_properties.author = "Codex"
doc.save(OUT)
print(OUT)
print(f"entities={len(all_entity_names)} fields={field_count} relations={len(relation_rows)}")
