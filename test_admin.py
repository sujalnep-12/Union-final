import urllib.request
import urllib.parse
import http.cookiejar
import re

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

req = urllib.request.Request('http://127.0.0.1:8000/signin/')
resp = opener.open(req)
html = resp.read().decode()
print('Signin page status:', resp.status)

csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
if csrf:
    csrf_token = csrf.group(1)
    print('CSRF token found')
    
    data = urllib.parse.urlencode({
        'csrfmiddlewaretoken': csrf_token,
        'email': 'union@admin.com',
        'password': 'damauli@2026',
    }).encode()
    
    req2 = urllib.request.Request('http://127.0.0.1:8000/signin/', data=data, headers={'Referer': 'http://127.0.0.1:8000/signin/'})
    resp2 = opener.open(req2)
    print('Signin post status:', resp2.status)
    print('Signin location:', resp2.getheader('Location', 'N/A'))
else:
    print('No CSRF token found')
