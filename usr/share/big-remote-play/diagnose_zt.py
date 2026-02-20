#!/usr/bin/env python3
import os
import json
import subprocess
import shutil

print("=== ZeroTier Diagnostic Tool ===")

# 1. Check Token
token_path = os.path.expanduser("~/.config/big-remoteplay/zerotier/api_token.txt")
print(f"Checking token file: {token_path}")

token = None
if os.path.exists(token_path):
    print("  [OK] File exists.")
    try:
        with open(token_path, 'r') as f:
            token = f.read().strip()
        print(f"  [OK] Token read (len={len(token)})")
    except Exception as e:
        print(f"  [ERR] Failed to read token: {e}")
else:
    print("  [ERR] valid token file NOT found.")

# 2. Test API
if token:
    print("\ntesting API connectivity (my.zerotier.com)...")
    url = "https://my.zerotier.com/api/network"
    cmd = ["curl", "-s", "-k", "-H", f"Authorization: bearer {token}", url]
    print(f"  Command: {' '.join(cmd)}")
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(f"  Return Code: {res.returncode}")
        print(f"  Output Preview: {res.stdout[:150]}...")
        
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                print(f"  JSON Parsed: Yes ({type(data)})")
                if isinstance(data, list):
                    print(f"  Networks Found: {len(data)}")
                    for net in data:
                        nid = net.get('id')
                        nname = net.get('config', {}).get('name', 'Unknown')
                        print(f"    - Network: {nname} ({nid})")
                        
                        # Test Members
                        url_m = f"https://my.zerotier.com/api/network/{nid}/member"
                        cmd_m = ["curl", "-s", "-k", "-H", f"Authorization: bearer {token}", url_m]
                        res_m = subprocess.run(cmd_m, capture_output=True, text=True)
                        
                        if res_m.returncode == 0:
                            m_data = json.loads(res_m.stdout)
                            print(f"      Members Found: {len(m_data)}")
                            for m in m_data:
                                print(f"        * {m.get('name')} | {m.get('nodeId')} | Online: {m.get('online')}")
                        else:
                            print(f"      [ERR] Failed to list members: {res_m.stderr}")
                else:
                    print(f"  [WARN] Unexpected JSON structure: {data}")
            except json.JSONDecodeError as je:
                print(f"  [ERR] Invalid JSON: {je}")
    except Exception as e:
        print(f"  [ERR] Subprocess failed: {e}")
else:
    print("\nSkipping API test (no token).")

# 3. Test CLI
print("\nChecking zerotier-cli...")
zt_path = shutil.which("zerotier-cli") or "/usr/sbin/zerotier-cli"
print(f"  Path: {zt_path}")

if os.path.exists(zt_path):
    print("  [OK] Binary found.")
    # Check if we can run info without root?
    print("  Trying 'info' (usually needs root or correct user group)...")
    res = subprocess.run([zt_path, "info"], capture_output=True, text=True)
    if res.returncode == 0:
        print(f"  [OK] Info: {res.stdout.strip()}")
    else:
        print(f"  [INFO] 'info' failed (expected if not root): {res.returncode}")
        # print("  Trying pkexec...")
        # cmd_pk = ["pkexec", zt_path, "info"]
        # res_pk = subprocess.run(cmd_pk, capture_output=True, text=True)
        # print(f"  Pkexec result: {res_pk.returncode}")
else:
    print("  [ERR] zerotier-cli NOT found.")

print("\n=== End Diagnosis ===")
