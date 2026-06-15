import sys
import os
from pathlib import Path

# Add project directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cloud_sync

def safe_print(msg):
    print(msg.encode('ascii', 'ignore').decode())

def test_download():
    safe_print("Testing Google Drive Connection...")
    sync = cloud_sync.GoogleDriveSync(None)
    if not sync.authenticate():
        safe_print("Authentication failed.")
        return
        
    safe_print("Authentication successful! Listing root files...")
    files = sync.list_files("root")
    safe_print(f"Found {len(files)} items at root:")
    
    excel_file = None
    pdf_file = None
    for item in files:
        safe_print(f" - Type: {item[0]}, Name: {item[1]}, ID: {item[2]}, Suffix: {item[4]}")
        if ".xls" in item[1].lower() or ".xlsx" in item[1].lower():
            excel_file = item
        if ".pdf" in item[1].lower():
            pdf_file = item
            
    # Try downloading the first excel file if found
    if excel_file:
        safe_print(f"\nTrying to download Excel file: {excel_file[1]} (ID: {excel_file[2]})")
        dest = Path("scratch/temp_test.xlsx")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        success = sync.download_binary(excel_file[2], dest)
        if success and dest.exists():
            safe_print(f"SUCCESS: Downloaded to {dest} ({dest.stat().st_size} bytes)")
        else:
            safe_print("FAILED to download Excel binary.")
    else:
        safe_print("\nNo Excel files found in the root directory. Let's search...")
        
    try:
        results = sync.service.files().list(
            q="mimeType='application/vnd.google-apps.spreadsheet' or name contains '.xlsx' or name contains '.xls'",
            fields="files(id, name, mimeType)"
        ).execute()
        files = results.get("files", [])
        safe_print(f"Search found {len(files)} spreadsheet files:")
        for f in files:
            safe_print(f" - Name: {f['name']}, ID: {f['id']}, Mime: {f['mimeType']}")
            dest = Path("scratch/temp_test.xlsx")
            if dest.exists():
                dest.unlink()
            safe_print(f"Downloading {f['name']}...")
            success = sync.download_binary(f['id'], dest)
            if success and dest.exists():
                safe_print(f"SUCCESS: Downloaded to {dest} ({dest.stat().st_size} bytes)")
                break
            else:
                safe_print("FAILED to download.")
    except Exception as e:
        safe_print(f"Error searching/downloading spreadsheets: {e}")

if __name__ == "__main__":
    test_download()
