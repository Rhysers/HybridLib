#!/opt/pymarc/.venv/bin/python3
import json
import os
import sys
import requests
from dotenv import load_dotenv
from pymarc import Field, Record, Subfield, record_to_xml

# Load credentials from .env
load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
PREVIEW = False  # Set to True to prompt on each book; False to run unattended

ABS_URL = os.getenv("ABS_URL", "https://audiobooks.library.odinforce.net")
ABS_API_KEY = os.getenv("ABS_API_KEY")

KOHA_URL = os.getenv("KOHA_URL", "https://staff.library.odinforce.net/api/v1")
KOHA_USER = os.getenv("KOHA_USER")
KOHA_PASS = os.getenv("KOHA_PASSWORD")

KOHA_BRANCH = "RCF"
KOHA_ITEMTYPE = "ABK"  # Replace with the exact Item Type code you created in Koha

abs_headers = {"Authorization": f"Bearer {ABS_API_KEY}", "Accept": "application/json"}
koha_auth = (KOHA_USER, KOHA_PASS)

# ==========================================
# 1. FETCH LIBRARIES FROM AUDIOBOOKSHELF
# ==========================================
print("Fetching libraries from Audiobookshelf...")
libs_resp = requests.get(f"{ABS_URL}/api/libraries", headers=abs_headers)
libs_resp.raise_for_status()
libraries = libs_resp.json().get("libraries", [])

print(f"Found {len(libraries)} libraries!")

# ==========================================
# 2. PROCESS AUDIOBOOKS BY LIBRARY
# ==========================================
for lib in libraries:
    lib_id = lib.get("id")
    lib_name = lib.get("name")
    media_type = lib.get("mediaType")

    # Audiobookshelf libraries can hold podcasts or books
    if media_type != "book":
        print(f"\nSkipping non-book library: {lib_name} (Type: {media_type})")
        continue

    print(f"\n--- Scanning Library: {lib_name} (ID: {lib_id}) ---")

    # Fetch items for this library
    items_resp = requests.get(f"{ABS_URL}/api/libraries/{lib_id}/items", headers=abs_headers)
    items_resp.raise_for_status()
    items = items_resp.json().get("results", [])

    print(f"  Found {len(items)} audiobooks in {lib_name}.")

    for item in items:
        item_id = item.get("id")
        media = item.get("media", {})
        metadata = media.get("metadata", {})

        title = metadata.get("title", "Unknown Title")
        author = metadata.get("authorName", "")
        narrator = metadata.get("narratorName", "")
        summary = metadata.get("description", "")
        barcode = f"ABS-{item_id}"

        # --- PRE-FLIGHT CHECK: Query Koha for existing AUDIOBOOK edition ---
        query = {"title": {"-like": f"%{title}%"}}
        check_resp = requests.get(
            f"{KOHA_URL}/biblios",
            params={"q": json.dumps(query)},
            headers={"Accept": "application/json"},
            auth=koha_auth,
        )

        if check_resp.ok:
            matches = check_resp.json()
            audiobook_exists = any(b.get("item_type") == KOHA_ITEMTYPE for b in matches)
            if audiobook_exists:
                print(f"  [-] Skipping '{title}' (Audiobook edition already cataloged in Koha)")
                continue

        # --- BUILD MARC RECORD ---
        rec = Record(force_utf8=True)
        # Leader: 'i' = non-musical sound recording
        rec.leader = "00000nim a2200000 u 4500"

        # 007 sz = sound recording
        rec.add_ordered_field(Field(tag="007", data="sz"))

        # 245 Title
        rec.add_ordered_field(
            Field(
                tag="245",
                indicators=["1", "0"],
                subfields=[Subfield(code="a", value=title)],
            )
        )

        # 100 Author
        if author:
            rec.add_ordered_field(
                Field(
                    tag="100",
                    indicators=["1", " "],
                    subfields=[Subfield(code="a", value=author)],
                )
            )

        # 700 Narrator (Added entry)
        if narrator:
            rec.add_ordered_field(
                Field(
                    tag="700",
                    indicators=["1", " "],
                    subfields=[
                        Subfield(code="a", value=narrator),
                        Subfield(code="e", value="narrator"),
                    ],
                )
            )

        # 520 Description / Summary
        if summary:
            rec.add_ordered_field(
                Field(
                    tag="520",
                    indicators=[" ", " "],
                    subfields=[Subfield(code="a", value=summary)],
                )
            )

        # 856 Direct Listening URL
        listen_url = f"{ABS_URL}/item/{item_id}"
        rec.add_ordered_field(
            Field(
                tag="856",
                indicators=["4", "0"],
                subfields=[
                    Subfield(code="u", value=listen_url),
                    Subfield(code="y", value=f"Listen Online in Audiobookshelf ({lib_name})"),
                ],
            )
        )

        # 942 Koha Record Item Type
        rec.add_ordered_field(
            Field(
                tag="942",
                indicators=[" ", " "],
                subfields=[Subfield(code="c", value=KOHA_ITEMTYPE)],
            )
        )

        # 952 Koha Virtual Holding Copy
        rec.add_ordered_field(
            Field(
                tag="952",
                indicators=[" ", " "],
                subfields=[
                    Subfield(code="a", value=KOHA_BRANCH),
                    Subfield(code="b", value=KOHA_BRANCH),
                    Subfield(code="y", value=KOHA_ITEMTYPE),
                    Subfield(code="o", value="ONLINE"),
                    Subfield(code="p", value=barcode),
                ],
            )
        )

        marcxml_bytes = record_to_xml(rec)

        # --- PREVIEW & PAUSE ---
        if PREVIEW:
            print(f"\n=== PREVIEWING MARCXML FOR: {title} ===")
            print(marcxml_bytes.decode("utf-8"))
            print("============================" + "=" * len(title))

            try:
                input(
                    f"Press [Enter] to inject '{title}' into Koha, or [Ctrl-C] to abort..."
                )
            except KeyboardInterrupt:
                print("\n\nScript aborted by user. Exiting.")
                sys.exit(0)

        # --- INJECT INTO KOHA ---
        koha_headers = {
            "Content-Type": "application/marcxml+xml",
            "Accept": "application/json",
        }
        koha_resp = requests.post(
            f"{KOHA_URL}/biblios",
            data=marcxml_bytes,
            headers=koha_headers,
            auth=koha_auth,
        )

        if koha_resp.status_code in [200, 201]:
            print(f"  [+] Successfully injected: {title}")
        else:
            print(
                f"  [!] Failed to inject {title}. Status: {koha_resp.status_code} - {koha_resp.text}"
            )

print("\nAudiobook sync complete!")

