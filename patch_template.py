from pathlib import Path
path = Path('templates/user-dashboard/community_detail.html')
text = path.read_text(encoding='utf-8')
old = '<form method="post" action="{% url \'hub:community_detail\' community.slug %}?tab=posts" class="inline-flex items-center">'
new = '<form data-like-form method="post" action="{% url \'hub:community_detail\' community.slug %}?tab=posts" class="inline-flex items-center">'
if old not in text:
    print('OLD not found')
else:
    path.write_text(text.replace(old, new), encoding='utf-8')
    print('patched like form')
