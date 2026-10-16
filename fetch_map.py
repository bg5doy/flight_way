import ssl, urllib.request, os, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

urls = [
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson",
    "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/ne_110m_admin_0_countries.geojson",
    "https://unpkg.com/@observablehq/nominatim-geojson@1.0.0/ne_110m_admin_0_countries.geojson",
]
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_ne110m.geojson")

for u in urls:
    try:
        print("trying:", u)
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=40) as r:
            data = r.read()
        j = json.loads(data.decode("utf-8"))
        n = len(j.get("features", []))
        print("features:", n, "bytes:", len(data))
        with open(out, "wb") as f:
            f.write(data)
        print("SAVED:", out)
        break
    except Exception as e:
        print("fail:", e)
