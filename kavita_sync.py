#!/opt/pymarc/.venv/bin/python3
import requests
import sys
import os
import json
from pymarc import Record, Field, Subfield, record_to_xml
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
PREVIEW = False # Set to True to prompt on each book; False to run unattended

KAVITA_URL = "https://ebooks.library.odinforce.net"
KAVITA_API_KEY = os.getenv("KAVITA_API_KEY")

KOHA_URL = "https://staff.library.odinforce.net/api/v1"
KOHA_USER = os.getenv("KOHA_USER", "root")
KOHA_PASS = os.getenv("KOHA_PASSWORD")

KOHA_BRANCH = "RCF"
KOHA_ITEMTYPE = "EBK"

# ==========================================
# 1. AUTHENTICATE WITH KAVITA
# ==========================================
print("Authenticating with Kavita...")
auth_resp = requests.post(
    f"{KAVITA_URL}/api/Plugin/authenticate",
    params={"apiKey": KAVITA_API_KEY, "pluginName": "KohaSync"}
)
auth_resp.raise_for_status()
token = auth_resp.json().get("token")
headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

# ==========================================
# 2. FETCH LIBRARIES & ALL SERIES
# ==========================================
print("Fetching libraries from Kavita...")
libs_resp = requests.get(f"{KAVITA_URL}/api/Library/libraries", headers=headers)
libs_resp.raise_for_status()
libraries = libs_resp.json()

print("Fetching all series from Kavita...")
series_resp = requests.post(f"{KAVITA_URL}/api/Series/all-v2", headers=headers, json={})
series_resp.raise_for_status()
all_series = series_resp.json()

# ==========================================
# 3. PROCESS AND SYNC
# ==========================================
koha_auth = (KOHA_USER, KOHA_PASS)

for lib in libraries:
    lib_id = lib.get("id")
    lib_name = lib.get("name")
    print(f"\n--- Scanning Library: {lib_name} (ID: {lib_id}) ---")
    
    lib_series = [s for s in all_series if s.get("libraryId") == lib_id]
    print(f"  Found {len(lib_series)} series in {lib_name}.")
    
    for series in lib_series:
        title = series.get("name", "Unknown Title")
        series_id = series.get("id")
        barcode = f"KAV-{series_id}"
        
        # --- PRE-FLIGHT CHECK: Query Koha for existing EBK edition ---
        query = {"title": {"-like": f"%{title}%"}}
        check_resp = requests.get(
            f"{KOHA_URL}/biblios",
            params={"q": json.dumps(query)},
            headers={"Accept": "application/json"},
            auth=koha_auth,
        )

        if check_resp.ok:
            matches = check_resp.json()
            # Only skip if one of the matched records is already an E-Book (EBK)
            ebook_exists = any(b.get("item_type") == KOHA_ITEMTYPE for b in matches)
            if ebook_exists:
                print(f"  [-] Skipping '{title}' (EBK edition already cataloged in Koha)")
                continue

        # --- FETCH METADATA ---
        meta_resp = requests.get(f"{KAVITA_URL}/api/Series/metadata", headers=headers, params={"seriesId": series_id})
        meta = meta_resp.json() if meta_resp.status_code == 200 else {}
        
        writers = meta.get("writers", [])
        author = writers[0].get("name", "") if writers else ""
        summary = meta.get("summary", "")

        # --- BUILD MARC RECORD ---
        rec = Record(force_utf8=True)
        rec.leader = '00000nam a2200000 u 4500'
        rec.add_ordered_field(Field(tag='007', data='cr'))
        rec.add_ordered_field(Field(tag='245', indicators=['1', '0'], subfields=[Subfield(code='a', value=title)]))
        if author: rec.add_ordered_field(Field(tag='100', indicators=['1', ' '], subfields=[Subfield(code='a', value=author)]))
        if summary: rec.add_ordered_field(Field(tag='520', indicators=[' ', ' '], subfields=[Subfield(code='a', value=summary)]))
        
        read_url = f"{KAVITA_URL}/library/{lib_id}/series/{series_id}"
        rec.add_ordered_field(Field(tag='856', indicators=['4', '0'], subfields=[
            Subfield(code='u', value=read_url),
            Subfield(code='y', value=f'Read Online in Kavita ({lib_name})')
        ]))
        
        rec.add_ordered_field(Field(tag='942', indicators=[' ', ' '], subfields=[Subfield(code='c', value=KOHA_ITEMTYPE)]))
        rec.add_ordered_field(Field(tag='952', indicators=[' ', ' '], subfields=[
            Subfield(code='a', value=KOHA_BRANCH),
            Subfield(code='b', value=KOHA_BRANCH),
            Subfield(code='y', value=KOHA_ITEMTYPE),
            Subfield(code='o', value='ONLINE'),
            Subfield(code='p', value=barcode)
        ]))

        marcxml_bytes = record_to_xml(rec)

        # --- PREVIEW & PAUSE ---
        if PREVIEW:
                print(f"\n=== PREVIEWING MARCXML FOR: {title} ===")
                print(marcxml_bytes.decode('utf-8'))
                print("============================" + "=" * len(title))

                try:
                    input(f"Press [Enter] to inject '{title}' into Koha, or [Ctrl-C] to abort...")
                except KeyboardInterrupt:
                    print("\n\nScript aborted by user. Exiting.")
                    sys.exit(0)

        # --- INJECT INTO KOHA ---
        koha_headers = {"Content-Type": "application/marcxml+xml", "Accept": "application/json"}
        koha_resp = requests.post(
            f"{KOHA_URL}/biblios", 
            data=marcxml_bytes, 
            headers=koha_headers, 
            auth=koha_auth
        )
        
        if koha_resp.status_code in [200, 201]:
            print(f"Successfully injected: {title}")
        else:
            print(f"Failed to inject {title}. Status: {koha_resp.status_code} - {koha_resp.text}")

