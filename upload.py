#!/usr/bin/env python3
"""
Tanlangan rasmlarni filebrowser saytiga YANGI papka qilib yuklaydi —
shunda ularni originallar kabi brauzerda ochib ko'rasiz / o'chirasiz.

Ishlatish:
  python upload.py <host> <parol> <lokal_papka> <masofaviy_papka>
Misol:
  python upload.py https://238.app.ioedge.host zMv76m8Zai8sYB_L \
      output/Fayzobod/camera_exit_1/selected dataset/_TANLANGAN/camera_exit_1
"""
import sys, os, json, urllib.request, urllib.parse


def login(host, pwd, user="admin"):
    data = json.dumps({"username": user, "password": pwd}).encode()
    req = urllib.request.Request(host + "/api/login", data=data,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=30).read().decode()


def mkdir(host, token, remote):
    # filebrowser: trailing slash = papka yaratish
    url = host + "/api/resources/" + urllib.parse.quote(remote) + "/?override=false"
    try:
        req = urllib.request.Request(url, data=b"", method="POST", headers={"X-Auth": token})
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass   # allaqachon bor bo'lsa — mayli


def upload_file(host, token, remote_path, data):
    url = host + "/api/resources/" + urllib.parse.quote(remote_path) + "?override=true"
    req = urllib.request.Request(url, data=data, method="POST", headers={"X-Auth": token})
    urllib.request.urlopen(req, timeout=120)


def main():
    host, pwd, localdir, remote = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].strip("/")
    host = host.rstrip("/")
    token = login(host, pwd)
    # papka ierarxiyasini yaratamiz
    parts = remote.split("/")
    for i in range(1, len(parts) + 1):
        mkdir(host, token, "/".join(parts[:i]))
    files = sorted(f for f in os.listdir(localdir) if f.lower().endswith((".png", ".jpg", ".jpeg")))
    print(f"{len(files)} ta rasm yuklanmoqda → {host}/files/{remote}/")
    ok = 0
    for f in files:
        try:
            with open(os.path.join(localdir, f), "rb") as fh:
                upload_file(host, token, remote + "/" + f, fh.read())
            ok += 1
            if ok % 20 == 0:
                print(f"  {ok}/{len(files)}")
        except Exception as e:
            print(f"  XATO {f}: {e}")
    print(f"✓ {ok}/{len(files)} yuklandi. Ko'rish: {host}/files/{remote}/")


if __name__ == "__main__":
    main()
