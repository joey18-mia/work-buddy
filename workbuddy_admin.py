"""
workbuddy_admin.py  —  called by workbuddy.bat
Usage:
  python workbuddy_admin.py list
  python workbuddy_admin.py uptime
"""
import sys
import json
import urllib.request
import urllib.error

def fetch_status():
    try:
        with urllib.request.urlopen("http://localhost:5000/api/admin/status", timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.URLError:
        return None

def cmd_list():
    data = fetch_status()
    if data is None:
        print("  [!] Server is not responding. Is it running?")
        return
    conns = data.get("connections", [])
    print()
    if not conns:
        print("  No active connections in the last 30 minutes.")
    else:
        print(f"  {len(conns)} device(s) seen in the last 30 minutes:")
        print()
        print(f"  {'IP':<20} {'Device / Hostname':<35} {'Last Seen'}")
        print(f"  {'-'*20} {'-'*35} {'-'*10}")
        for c in conns:
            name = c["hostname"] if c["hostname"] != c["ip"] else "(unknown)"
            print(f"  {c['ip']:<20} {name:<35} {c['last_seen']}")
    print()

def cmd_uptime():
    data = fetch_status()
    if data is None:
        print("  [!] Server is not responding. Is it running?")
        return
    print()
    print(f"  Server uptime: {data.get('uptime', 'unknown')}")
    print()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python workbuddy_admin.py [list|uptime]")
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd == "list":
        cmd_list()
    elif cmd == "uptime":
        cmd_uptime()
    else:
        print(f"Unknown command: {cmd}")
